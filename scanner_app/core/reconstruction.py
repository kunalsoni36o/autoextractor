from __future__ import annotations

import json
import re
import struct
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from PySide6.QtCore import QThread, Signal

from .colmap import ColmapCapabilities
from .profiles import SAFE_SCAN, ReconstructionProfile


class CancelledError(RuntimeError):
    pass


class CommandRunner(Protocol):
    def __call__(self, command: list[str], cwd: Path, on_output: Callable[[str], None], cancel_event: threading.Event) -> "ProcessResult": ...


@dataclass
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class StageResult:
    name: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    expected_artifacts: list[str] = field(default_factory=list)
    produced_artifacts: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    note: str | None = None


@dataclass
class PipelineConfig:
    executable: Path
    images: Path
    workspace: Path
    mapper: str = "incremental"
    mesher: str = "poisson"
    second_mesh: bool = False
    use_gpu: bool = True
    poisson_depth: int = 5
    profile: str = "explicit"
    profile_settings: ReconstructionProfile | None = None
    min_registered_ratio: float = 0.5


@dataclass(frozen=True)
class SparseModelStats:
    path: Path
    registered_images: int | None
    points3d: int | None


def _read_images_count(path: Path) -> int | None:
    try:
        data = path.read_bytes(); offset = 8; count = 0
        if len(data) < offset: return None
        while offset + 64 <= len(data):
            offset += 4 + 8 * 4 + 8 * 3 + 4
            end = data.find(b"\x00", offset)
            if end < 0: return None
            offset = end + 1
            if offset + 8 > len(data): return None
            points = struct.unpack_from("<Q", data, offset)[0]; offset += 8 + points * 24
            if offset > len(data): return None
            count += 1
        return count
    except (OSError, struct.error):
        return None


def _read_points_count(path: Path) -> int | None:
    try:
        data = path.read_bytes(); offset = 8; count = 0
        while offset + 43 <= len(data):
            offset += 8 + 24 + 3 + 8
            if offset + 8 > len(data): return None
            track = struct.unpack_from("<Q", data, offset)[0]; offset += 8 + track * 8
            if offset > len(data): return None
            count += 1
        return count
    except (OSError, struct.error):
        return None


def enumerate_sparse_models(root: Path) -> list[SparseModelStats]:
    models: list[SparseModelStats] = []
    if not root.exists(): return models
    for candidate in sorted(path for path in root.iterdir() if path.is_dir()):
        cameras = candidate / "cameras.bin"; images = candidate / "images.bin"; points = candidate / "points3D.bin"
        if cameras.is_file() and images.is_file():
            models.append(SparseModelStats(candidate, _read_images_count(images), _read_points_count(points) if points.is_file() else None))
    return models


def select_best_sparse_model(root: Path) -> SparseModelStats | None:
    models = enumerate_sparse_models(root)
    return max(models, key=lambda item: (item.registered_images if item.registered_images is not None else -1, item.points3d if item.points3d is not None else -1), default=None)


@dataclass
class PipelineReport:
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    stages: list[StageResult]
    artifacts: list[str]
    report_path: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_sparse_model(root: Path) -> Path | None:
    selected = select_best_sparse_model(root)
    return selected.path if selected else None


def _files(paths: list[Path]) -> list[str]:
    return [str(path) for path in paths if path.exists() and (path.is_file() or path.is_dir())]


def _valid_nonempty_ply(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        header = path.read_bytes()[:16384].decode("ascii", errors="ignore")
    except OSError:
        return False
    match = re.search(r"(?:^|\n)element\s+vertex\s+(\d+)(?:\r?\n|$)", header)
    return bool(match and int(match.group(1)) > 0 and "end_header" in header)


def _validate_images(images: Path) -> None:
    supported = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
    if not images.is_dir():
        raise ValueError(f"Image folder does not exist: {images}")
    if not any(path.is_file() and path.suffix.lower() in supported for path in images.iterdir()):
        raise ValueError(f"No supported images found in: {images}")


def _cached_inputs_match(config: PipelineConfig) -> bool:
    """Accept cached feature/match data only when it belongs to this project."""
    manifest = config.workspace.parent.parent / "project.json"
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_names = sorted(data.get("images", []))
        current_names = sorted(path.name for path in config.images.iterdir() if path.is_file())
        return bool(manifest_names) and manifest_names == current_names
    except (OSError, json.JSONDecodeError):
        return False


def _validate_stage(name: str, config: PipelineConfig, sparse_model: Path | None) -> tuple[list[Path], Path | None]:
    root = config.workspace
    if name == "Validate input":
        _validate_images(config.images); return [config.images], sparse_model
    if name == "Prepare workspace":
        return [root], sparse_model
    if name == "Extract features":
        db = root / "database.db"
        if not db.is_file() or db.stat().st_size == 0: raise RuntimeError(f"Feature database was not produced: {db}")
        return [db], sparse_model
    if name == "Match features":
        db = root / "database.db"
        marker = root / ".stages" / "match_features.ok"
        if not db.is_file() or db.stat().st_size == 0 or not marker.exists(): raise RuntimeError(f"Matched feature state is not validated: {db}")
        return [db, marker], sparse_model
    if name == "Sparse reconstruction":
        sparse_model = find_sparse_model(root / "sparse")
        if sparse_model is None: raise RuntimeError(f"No valid sparse model found under {root / 'sparse'}")
        stats = select_best_sparse_model(root / "sparse")
        total_images = len([p for p in config.images.iterdir() if p.is_file()]) if config.images.is_dir() else 0
        if stats and stats.registered_images is not None and total_images and stats.registered_images / total_images < config.min_registered_ratio:
            raise RuntimeError(f"Sparse reconstruction registered only {stats.registered_images}/{total_images} images; at least {config.min_registered_ratio:.0%} are required. Add overlapping views or use a better capture set.")
        return [sparse_model], sparse_model
    if name == "Validate sparse model":
        if sparse_model is None: raise RuntimeError("Sparse model is unavailable")
        return [sparse_model / "cameras.bin", sparse_model / "images.bin"], sparse_model
    if name == "Undistort images":
        dense = root / "dense"
        if not (dense / "images").is_dir() or not (dense / "sparse").is_dir(): raise RuntimeError(f"Dense workspace was not prepared: {dense}")
        return [dense], sparse_model
    if name == "PatchMatch Stereo":
        depth = root / "dense" / "stereo" / "depth_maps"
        if not depth.exists(): raise RuntimeError(f"PatchMatch depth output is missing: {depth}")
        return [depth], sparse_model
    if name == "Fuse depth maps":
        fused = root / "dense" / "fused.ply"
        if not _valid_nonempty_ply(fused): raise RuntimeError(f"Fused point cloud is missing, empty, or contains zero vertices: {fused}")
        return [fused], sparse_model
    if name == "Poisson meshing":
        mesh = root / "exports" / "mesh_poisson.ply"
        if not mesh.is_file() or mesh.stat().st_size == 0: raise RuntimeError(f"Poisson mesh was not produced or is empty: {mesh}")
        return [mesh], sparse_model
    if name == "Delaunay meshing":
        mesh = root / "exports" / "mesh_delaunay.ply"
        if not mesh.is_file() or mesh.stat().st_size == 0: raise RuntimeError(f"Delaunay mesh was not produced or is empty: {mesh}")
        return [mesh], sparse_model
    return [], sparse_model


def _stage_commands(config: PipelineConfig) -> list[tuple[str, list[str], list[Path]]]:
    root, db, sparse, dense = config.workspace, config.workspace / "database.db", config.workspace / "sparse", config.workspace / "dense"
    commands: list[tuple[str, list[str], list[Path]]] = [
        ("Validate input", [], [config.images]),
        ("Prepare workspace", [], [root]),
        ("Extract features", ["feature_extractor", "--database_path", str(db), "--image_path", str(config.images)], [db]),
        ("Match features", ["exhaustive_matcher", "--database_path", str(db)], [db]),
    ]
    mapper = "global_mapper" if config.mapper == "global" else "mapper"
    commands.append(("Sparse reconstruction", [mapper, "--image_path", str(config.images), "--database_path", str(db), "--output_path", str(sparse)], [sparse]))
    commands.append(("Validate sparse model", [], [sparse]))
    commands.extend([
        ("Undistort images", ["image_undistorter", "--image_path", str(config.images), "--input_path", "{SPARSE_MODEL}", "--output_path", str(dense), "--output_type", "COLMAP"], [dense]),
        ("PatchMatch Stereo", ["patch_match_stereo", "--workspace_path", str(dense)], [dense / "stereo" / "depth_maps"]),
        ("Fuse depth maps", ["stereo_fusion", "--workspace_path", str(dense), "--output_path", str(dense / "fused.ply")], [dense / "fused.ply"]),
    ])
    if config.mesher == "delaunay":
        commands.append(("Delaunay meshing", ["delaunay_mesher", "--input_path", str(dense), "--output_path", str(root / "exports" / "mesh_delaunay.ply")], [root / "exports" / "mesh_delaunay.ply"]))
    else:
        commands.append(("Poisson meshing", ["poisson_mesher", "--input_path", str(dense / "fused.ply"), "--output_path", str(root / "exports" / "mesh_poisson.ply"), "--PoissonMeshing.depth", str(config.poisson_depth)], [root / "exports" / "mesh_poisson.ply"]))
    if config.second_mesh:
        if config.mesher == "delaunay":
            commands.append(("Poisson meshing", ["poisson_mesher", "--input_path", str(dense / "fused.ply"), "--output_path", str(root / "exports" / "mesh_poisson.ply"), "--PoissonMeshing.depth", str(config.poisson_depth)], [root / "exports" / "mesh_poisson.ply"]))
        else:
            commands.append(("Delaunay meshing", ["delaunay_mesher", "--input_path", str(dense), "--output_path", str(root / "exports" / "mesh_delaunay.ply")], [root / "exports" / "mesh_delaunay.ply"]))
    return commands


def build_automatic_command(config: PipelineConfig, capabilities: ColmapCapabilities | None = None) -> list[str]:
    """Build only flags verified by the installed automatic_reconstructor help."""
    profile = config.profile_settings or SAFE_SCAN
    if capabilities is not None:
        if not capabilities.supports_command("automatic_reconstructor"):
            raise RuntimeError("Installed COLMAP does not support automatic_reconstructor")
        if not capabilities.supports_choice("--quality", profile.quality):
            raise RuntimeError("Installed COLMAP does not expose the verified low-quality automatic reconstruction profile")

    command = [str(config.executable), "automatic_reconstructor", "--workspace_path", str(config.workspace), "--image_path", str(config.images)]
    requested = [
        ("--data_type", profile.data_type),
        ("--quality", profile.quality),
        ("--mapper", profile.mapper),
        ("--mesher", profile.mesher),
        ("--num_threads", str(profile.num_threads)),
        ("--use_gpu", "1" if config.use_gpu else "0"),
    ]
    for flag, value in requested:
        if capabilities is None or capabilities.supports_flag(flag):
            command.extend([flag, value])
    return command


def _gpu_failure_hint(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ("out of memory", "cuda error", "cublas", "cudnn", "gpu memory")):
        return " Reduce image size or use the Safe Scan (Low VRAM) profile with GPU disabled."
    return ""


def run_automatic_pipeline(config: PipelineConfig, capabilities: ColmapCapabilities | None, runner: CommandRunner, on_stage: Callable[[str, str], None] | None = None, on_output: Callable[[str], None] | None = None, cancel_event: threading.Event | None = None, resume: bool = True) -> PipelineReport:
    """Run COLMAP's native automatic workflow while validating its outputs."""
    cancel_event = cancel_event or threading.Event()
    started = time.monotonic(); started_at = _now(); stages: list[StageResult] = []; artifacts: list[str] = []
    config.workspace.mkdir(parents=True, exist_ok=True); (config.workspace / "reports").mkdir(parents=True, exist_ok=True)
    status = "completed"; current_name = "Automatic reconstruction"; current_command: list[str] = []
    try:
        for name in ("Validate input", "Prepare workspace"):
            stage_started = _now(); on_stage and on_stage(name, "running")
            if name == "Validate input": _validate_images(config.images); produced = [config.images]
            else: config.workspace.mkdir(parents=True, exist_ok=True); produced = [config.workspace]
            result = StageResult(name, "completed", stage_started, _now(), 0.0, [], 0, [str(p) for p in produced], _files(produced))
            stages.append(result); artifacts.extend(result.produced_artifacts); on_stage and on_stage(name, "completed")

        current_name = "Automatic reconstruction"; current_command = build_automatic_command(config, capabilities)
        on_stage and on_stage(current_name, "running")
        process = runner(current_command, config.workspace, lambda line: on_output and on_output(line), cancel_event)
        details = process.stderr or process.stdout
        if process.returncode != 0:
            raise RuntimeError(f"Automatic reconstruction failed with exit code {process.returncode}: {details[-4000:]}{_gpu_failure_hint(details)}")

        fused = config.workspace / "dense" / "fused.ply"
        selected = select_best_sparse_model(config.workspace / "sparse")
        total_images = len([p for p in config.images.iterdir() if p.is_file()])
        if selected is None:
            raise RuntimeError(f"Automatic reconstruction did not produce a valid sparse model under {config.workspace / 'sparse'}")
        if selected.registered_images is not None and total_images and selected.registered_images / total_images < config.min_registered_ratio:
            raise RuntimeError(f"Sparse reconstruction registered only {selected.registered_images}/{total_images} images; at least {config.min_registered_ratio:.0%} are required. Add overlapping views or use a better capture set.")
        if not _valid_nonempty_ply(fused):
            raise RuntimeError(f"Automatic reconstruction produced an empty or missing fused point cloud: {fused}")
        mesh_candidates = [p for p in config.workspace.rglob("*.ply") if p != fused and p.is_file() and p.stat().st_size > 0]
        if not any(config.mesher in p.name.lower() for p in mesh_candidates):
            raise RuntimeError(f"Automatic reconstruction did not produce a non-empty {config.mesher} mesh")
        produced = [selected.path, fused, *[p for p in mesh_candidates if config.mesher in p.name.lower()]]
        result = StageResult(current_name, "completed", _now(), _now(), 0.0, current_command, process.returncode, [str(config.workspace / "dense" / "fused.ply")], _files(produced), process.stdout, process.stderr, note="Safe Scan (Low VRAM) automatic profile")
        stages.append(result); artifacts.extend(result.produced_artifacts); on_stage and on_stage(current_name, "completed")
        metadata = {"profile": (config.profile_settings or SAFE_SCAN).name, "selected_sparse_model": str(selected.path), "registered_images": selected.registered_images, "points3d": selected.points3d, "input_images": total_images}
    except CancelledError as exc:
        status = "cancelled"; on_stage and on_stage(current_name, "cancelled"); stages.append(StageResult(current_name, "cancelled", _now(), _now(), 0.0, current_command, error=str(exc))); metadata = {}
    except Exception as exc:
        status = "failed"; on_stage and on_stage(current_name, "failed")
        process = locals().get("process")
        stages.append(StageResult(current_name, "failed", _now(), _now(), 0.0, current_command, process.returncode if process else None, stdout=process.stdout if process else "", stderr=process.stderr if process else "", error=str(exc))); metadata = {}
    report = PipelineReport(status, started_at, _now(), time.monotonic() - started, stages, sorted(set(artifacts)), metadata=metadata)
    report_path = config.workspace / "reports" / "reconstruction_report.json"; report.report_path = str(report_path); report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return report


def run_pipeline(config: PipelineConfig, runner: CommandRunner, on_stage: Callable[[str, str], None] | None = None, on_output: Callable[[str], None] | None = None, cancel_event: threading.Event | None = None, resume: bool = True) -> PipelineReport:
    cancel_event = cancel_event or threading.Event(); started = time.monotonic(); started_at = _now(); stages: list[StageResult] = []; artifacts: list[str] = []; sparse_model: Path | None = find_sparse_model(config.workspace / "sparse")
    config.workspace.mkdir(parents=True, exist_ok=True); (config.workspace / "exports").mkdir(parents=True, exist_ok=True); (config.workspace / "reports").mkdir(parents=True, exist_ok=True); (config.workspace / ".stages").mkdir(parents=True, exist_ok=True)
    status = "completed"
    current_name = "Pipeline"
    current_command: list[str] = []
    current_started = _now()
    current_process: ProcessResult | None = None
    try:
        for name, raw_command, expected in _stage_commands(config):
            current_name = name
            current_process = None
            if cancel_event.is_set(): raise CancelledError("Pipeline cancelled before the next stage started")
            command = list(raw_command)
            if "{SPARSE_MODEL}" in command:
                if sparse_model is None: sparse_model = find_sparse_model(config.workspace / "sparse")
                if sparse_model is None: raise RuntimeError("Cannot undistort without a valid sparse model")
                command = [str(sparse_model) if item == "{SPARSE_MODEL}" else item for item in command]
            stage_start = time.monotonic(); stage_started = _now()
            current_command = [str(config.executable), *command]
            current_started = stage_started
            if resume:
                try:
                    existing, sparse_model = _validate_stage(name, config, sparse_model)
                except (ValueError, RuntimeError):
                    existing = []
                if name in {"Extract features", "Match features"} and existing and not _cached_inputs_match(config):
                    existing = []
            else:
                existing = []
            dense_resume = _valid_nonempty_ply(config.workspace / "dense" / "fused.ply") and (config.workspace / "dense" / "images").is_dir() and find_sparse_model(config.workspace / "sparse") is not None
            prior_dense_stage = {"Validate input", "Prepare workspace", "Extract features", "Match features", "Sparse reconstruction", "Validate sparse model", "Undistort images", "PatchMatch Stereo", "Fuse depth maps"}
            if dense_resume and name in prior_dense_stage:
                existing_artifacts = _files(expected)
                result = StageResult(name, "skipped", stage_started, _now(), 0.0, command, 0, [str(p) for p in expected], existing_artifacts, note="Resumed from validated fused point cloud")
                stages.append(result); artifacts.extend(existing_artifacts); on_stage and on_stage(name, "skipped"); continue
            if existing and name not in {"Validate input", "Prepare workspace", "Validate sparse model"}:
                result = StageResult(name, "skipped", stage_started, _now(), 0.0, command, 0, [str(p) for p in expected], _files(existing), note="Compatible artifacts already exist")
                stages.append(result); artifacts.extend(result.produced_artifacts); on_stage and on_stage(name, "skipped"); continue
            on_stage and on_stage(name, "running")
            output_text = ""
            if name == "Sparse reconstruction":
                _validate_images(config.images)
                database = config.workspace / "database.db"
                if not database.is_file() or database.stat().st_size == 0:
                    raise RuntimeError(f"Mapper database is missing or empty: {database}")
                output_index = command.index("--output_path") + 1
                mapper_output = Path(command[output_index])
                mapper_output.mkdir(parents=True, exist_ok=True)
                if not mapper_output.is_dir():
                    raise RuntimeError(f"Mapper output path is not a directory: {mapper_output}")
            if name in {"Poisson meshing", "Delaunay meshing"}:
                dense = config.workspace / "dense"
                fused = dense / "fused.ply"
                if not _valid_nonempty_ply(fused):
                    raise RuntimeError(f"Cannot start {name}: fused point cloud is missing or empty, or contains zero vertices: {fused}")
                (config.workspace / "exports").mkdir(parents=True, exist_ok=True)
                if name == "Delaunay meshing":
                    dense_sparse = dense / "sparse"
                    visibility = dense / "stereo" / "consistency_graphs"
                    if not (dense_sparse / "cameras.bin").is_file() or not (dense_sparse / "images.bin").is_file():
                        raise RuntimeError(f"Cannot start Delaunay meshing: dense sparse reconstruction is incomplete: {dense_sparse}")
                    if not visibility.is_dir():
                        raise RuntimeError(f"Cannot start Delaunay meshing: dense visibility data is missing: {visibility}")
            if command:
                current_process = runner([str(config.executable), *command], config.workspace, lambda line: on_output and on_output(line), cancel_event)
                output_text = current_process.stdout
                if current_process.returncode != 0:
                    details = current_process.stderr or current_process.stdout[-2000:]
                    raise RuntimeError(f"{name} failed with exit code {current_process.returncode}: {details}{_gpu_failure_hint(details)}")
                if name == "Match features":
                    (config.workspace / ".stages" / "match_features.ok").write_text(_now(), encoding="utf-8")
            produced, sparse_model = _validate_stage(name, config, sparse_model)
            result = StageResult(name, "completed", stage_started, _now(), time.monotonic() - stage_start, [str(config.executable), *command], 0, [str(p) for p in expected], _files(produced), output_text, "")
            stages.append(result); artifacts.extend(result.produced_artifacts)
            if command:
                (config.workspace / ".stages" / (name.lower().replace(" ", "_") + ".ok")).write_text(_now(), encoding="utf-8")
            on_stage and on_stage(name, "completed")
    except CancelledError as exc:
        status = "cancelled"; on_stage and on_stage(current_name, "cancelled"); stages.append(StageResult(current_name, "cancelled", current_started, _now(), 0.0, current_command, error=str(exc)))
    except Exception as exc:
        status = "failed"; on_stage and on_stage(current_name, "failed"); stages.append(StageResult(current_name, "failed", current_started, _now(), 0.0, current_command, current_process.returncode if current_process else None, stdout=current_process.stdout if current_process else "", stderr=current_process.stderr if current_process else "", error=str(exc)))
    report = PipelineReport(status, started_at, _now(), time.monotonic() - started, stages, sorted(set(artifacts)), metadata={"strategy": "Explicit Safe Scan" if config.profile == "explicit_safe" else "Advanced Explicit"})
    report_path = config.workspace / "reports" / "reconstruction_report.json"; report.report_path = str(report_path); report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return report


def subprocess_runner(command: list[str], cwd: Path, on_output: Callable[[str], None], cancel_event: threading.Event) -> ProcessResult:
    process = subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    stdout_lines: list[str] = []; stderr_lines: list[str] = []

    def drain(stream, target: list[str]) -> None:
        for line in iter(stream.readline, ""):
            target.append(line)
            on_output(line.rstrip())
        stream.close()

    assert process.stdout is not None and process.stderr is not None
    stdout_thread = threading.Thread(target=drain, args=(process.stdout, stdout_lines), daemon=True)
    stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_lines), daemon=True)
    stdout_thread.start(); stderr_thread.start()
    while process.poll() is None:
        if cancel_event.is_set():
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            stdout_thread.join(timeout=2); stderr_thread.join(timeout=2)
            raise CancelledError("Native COLMAP process terminated by user")
        time.sleep(0.05)
    stdout_thread.join(); stderr_thread.join()
    return ProcessResult(process.returncode or 0, "".join(stdout_lines), "".join(stderr_lines))


class ReconstructionWorker(QThread):
    stage_changed = Signal(str, str)
    output_received = Signal(str)
    completed = Signal(object)

    def __init__(self, config: PipelineConfig, capabilities: ColmapCapabilities | None = None, resume: bool = True) -> None:
        super().__init__(); self.config = config; self.capabilities = capabilities; self.resume = resume; self.cancel_event = threading.Event()

    def run(self) -> None:
        if self.config.profile == "safe_scan":
            try:
                build_automatic_command(self.config, self.capabilities)
            except RuntimeError as automatic_error:
                fallback = replace(self.config, profile="explicit_safe")
                self.output_received.emit(f"Automatic LOW unavailable; falling back to Explicit Safe Scan: {automatic_error}")
                report = run_pipeline(fallback, subprocess_runner, self.stage_changed.emit, self.output_received.emit, self.cancel_event, self.resume)
                report.metadata["automatic_fallback_reason"] = str(automatic_error)
                if report.stages:
                    report.stages[0].note = f"Explicit Safe Scan fallback: {automatic_error}"
                if report.report_path:
                    Path(report.report_path).write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
            else:
                report = run_automatic_pipeline(self.config, self.capabilities, subprocess_runner, self.stage_changed.emit, self.output_received.emit, self.cancel_event, self.resume)
        else:
            report = run_pipeline(self.config, subprocess_runner, self.stage_changed.emit, self.output_received.emit, self.cancel_event, self.resume)
        self.completed.emit(report)

    def cancel(self) -> None:
        self.cancel_event.set()
