import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from scanner_app.core.colmap import ColmapCapabilities
from scanner_app.core.profiles import SAFE_SCAN
from scanner_app.core.reconstruction import (
    PipelineConfig, ProcessResult, SparseModelStats, build_automatic_command,
    run_automatic_pipeline, run_pipeline, select_best_sparse_model,
)

VALID_PLY = b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nelement face 0\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n"


class FakeRunner:
    def __init__(self, fail_on: str | None = None):
        self.commands: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, command, cwd, on_output, cancel_event):
        self.commands.append(command)
        stage = command[1]
        if stage == self.fail_on:
            return ProcessResult(9, "", f"simulated {stage} failure")
        if stage == "feature_extractor" or stage == "exhaustive_matcher":
            Path(command[command.index("--database_path") + 1]).write_bytes(b"database")
        elif stage in {"mapper", "global_mapper"}:
            model = Path(command[command.index("--output_path") + 1]) / "0"
            model.mkdir(parents=True, exist_ok=True)
            (model / "cameras.bin").write_bytes(b"camera")
            (model / "images.bin").write_bytes(b"images")
            (model / "points3D.bin").write_bytes(b"points")
        elif stage == "image_undistorter":
            dense = Path(command[command.index("--output_path") + 1])
            (dense / "images").mkdir(parents=True, exist_ok=True)
            (dense / "sparse").mkdir(parents=True, exist_ok=True)
        elif stage == "patch_match_stereo":
            (Path(command[command.index("--workspace_path") + 1]) / "stereo" / "depth_maps").mkdir(parents=True, exist_ok=True)
        elif stage == "stereo_fusion":
            Path(command[command.index("--output_path") + 1]).write_bytes(VALID_PLY)
        elif stage in {"poisson_mesher", "delaunay_mesher"}:
            Path(command[command.index("--output_path") + 1]).write_bytes(b"mesh")
        on_output(f"fake {stage}")
        return ProcessResult(0, f"fake {stage}", "")


class MapperDirectoryRunner(FakeRunner):
    def __call__(self, command, cwd, on_output, cancel_event):
        if command[1] in {"mapper", "global_mapper"}:
            output = Path(command[command.index("--output_path") + 1])
            if not output.is_dir():
                return ProcessResult(1, "", "output_path is not a directory")
        return super().__call__(command, cwd, on_output, cancel_event)


class NoFusionOutputRunner(FakeRunner):
    def __call__(self, command, cwd, on_output, cancel_event):
        if command[1] == "stereo_fusion":
            self.commands.append(command)
            return ProcessResult(0, "fusion completed without a point cloud", "")
        return super().__call__(command, cwd, on_output, cancel_event)


def make_config(root: Path) -> PipelineConfig:
    images = root / "images"; images.mkdir(); (images / "one.jpg").write_bytes(b"image")
    return PipelineConfig(Path("colmap.exe"), images, root / "workspace")


class ReconstructionTests(unittest.TestCase):
    def test_safe_scan_runs_automatic_reconstructor_and_validates_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = make_config(root); config.profile = "safe_scan"; config.profile_settings = SAFE_SCAN
            capabilities = ColmapCapabilities(Path("colmap.exe"), "4.2", frozenset({"automatic_reconstructor"}), True, help_text="automatic_reconstructor --quality low")

            def automatic_runner(command, cwd, on_output, cancel_event):
                self.assertEqual(command[1], "automatic_reconstructor")
                sparse = config.workspace / "sparse" / "0"; sparse.mkdir(parents=True)
                (sparse / "cameras.bin").write_bytes(b"camera"); (sparse / "images.bin").write_bytes(b"images")
                dense = config.workspace / "dense"; dense.mkdir(parents=True)
                (dense / "fused.ply").write_bytes(VALID_PLY)
                (config.workspace / "meshed-poisson.ply").write_bytes(b"mesh")
                return ProcessResult(0, "automatic complete", "")

            report = run_automatic_pipeline(config, capabilities, automatic_runner)
            self.assertEqual(report.status, "completed")
            self.assertEqual(report.metadata["profile"], SAFE_SCAN.name)

    def test_safe_scan_builds_verified_automatic_command_with_spaced_paths(self):
        with tempfile.TemporaryDirectory(prefix="scan with spaces ") as temp:
            root = Path(temp); config = make_config(root)
            config.profile = "safe_scan"; config.profile_settings = SAFE_SCAN
            capabilities = ColmapCapabilities(Path("colmap.exe"), "4.2", frozenset({"automatic_reconstructor"}), True, help_text="automatic_reconstructor --quality low --data_type --mapper --mesher --num_threads --use_gpu")
            command = build_automatic_command(config, capabilities)
            self.assertEqual(command[1], "automatic_reconstructor")
            self.assertIn("--quality", command); self.assertIn("low", command)
            self.assertIn(str(config.images), command); self.assertIn(str(config.workspace), command)

    def test_safe_scan_rejects_unverified_low_quality_syntax(self):
        with tempfile.TemporaryDirectory() as temp:
            config = make_config(Path(temp)); config.profile = "safe_scan"; config.profile_settings = SAFE_SCAN
            capabilities = ColmapCapabilities(Path("colmap.exe"), "4.2", frozenset({"automatic_reconstructor"}), True, help_text="automatic_reconstructor")
            with self.assertRaisesRegex(RuntimeError, "verified low-quality"):
                build_automatic_command(config, capabilities)

    def test_sparse_model_selection_prefers_registered_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sparse"
            for name in ("0", "1"):
                model = root / name; model.mkdir(parents=True)
                (model / "cameras.bin").write_bytes(b"camera")
                (model / "images.bin").write_bytes(b"images")
            stats = [SparseModelStats(root / "0", 3, 100), SparseModelStats(root / "1", 8, 50)]
            with patch("scanner_app.core.reconstruction.enumerate_sparse_models", return_value=stats):
                self.assertEqual(select_best_sparse_model(root).path.name, "1")

    def test_low_sparse_coverage_blocks_dense_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = make_config(root)
            for index in range(2, 90):
                (config.images / f"image_{index}.jpg").write_bytes(b"image")
            sparse = config.workspace / "sparse" / "0"
            stats = SparseModelStats(sparse, 3, 382)
            runner = FakeRunner()
            with patch("scanner_app.core.reconstruction.select_best_sparse_model", return_value=stats):
                report = run_pipeline(config, runner)
            self.assertEqual(report.status, "failed")
            self.assertIn("3/89", report.stages[-1].error)
            self.assertNotIn("image_undistorter", [command[1] for command in runner.commands])

    def test_pipeline_advances_through_all_stages(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = FakeRunner(); report = run_pipeline(make_config(Path(temp)), runner)
            self.assertEqual(report.status, "completed")
            self.assertEqual([s.status for s in report.stages].count("completed"), 10)
            self.assertTrue(any(path.endswith("fused.ply") for path in report.artifacts))
            self.assertTrue(any(path.endswith("mesh_poisson.ply") for path in report.artifacts))
            poisson = next(command for command in runner.commands if command[1] == "poisson_mesher")
            self.assertTrue(poisson[poisson.index("--input_path") + 1].endswith("dense\\fused.ply") or poisson[poisson.index("--input_path") + 1].endswith("dense/fused.ply"))

    def test_sparse_failure_prevents_dense_and_mesh(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = FakeRunner("mapper"); report = run_pipeline(make_config(Path(temp)), runner)
            self.assertEqual(report.status, "failed")
            self.assertNotIn("image_undistorter", [command[1] for command in runner.commands])

    def test_mapper_output_directory_is_created_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            config = make_config(Path(temp)); runner = MapperDirectoryRunner(); report = run_pipeline(config, runner)
            self.assertEqual(report.status, "completed")
            mapper_command = next(command for command in runner.commands if command[1] == "mapper")
            output = Path(mapper_command[mapper_command.index("--output_path") + 1])
            self.assertTrue(output.is_dir())

    def test_dense_failure_prevents_meshing(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = FakeRunner("patch_match_stereo"); report = run_pipeline(make_config(Path(temp)), runner)
            self.assertEqual(report.status, "failed")
            self.assertNotIn("poisson_mesher", [command[1] for command in runner.commands])

    def test_existing_fused_cloud_resumes_at_meshing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = make_config(root); workspace = config.workspace; dense = workspace / "dense"; (workspace / "sparse" / "0").mkdir(parents=True); (workspace / "sparse" / "0" / "cameras.bin").write_bytes(b"x"); (workspace / "sparse" / "0" / "images.bin").write_bytes(b"x"); (dense / "images").mkdir(parents=True); (dense / "sparse").mkdir(parents=True); (dense / "stereo" / "depth_maps").mkdir(parents=True); (dense / "fused.ply").write_bytes(VALID_PLY)
            runner = FakeRunner(); report = run_pipeline(config, runner)
            self.assertEqual(report.status, "completed")
            self.assertEqual([command[1] for command in runner.commands], ["poisson_mesher"])

    def test_delaunay_uses_dense_workspace_and_creates_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = make_config(root); config.mesher = "delaunay"; workspace = config.workspace; dense = workspace / "dense"; (workspace / "sparse" / "0").mkdir(parents=True); (workspace / "sparse" / "0" / "cameras.bin").write_bytes(b"x"); (workspace / "sparse" / "0" / "images.bin").write_bytes(b"x"); (dense / "images").mkdir(parents=True); (dense / "sparse").mkdir(parents=True); (dense / "sparse" / "cameras.bin").write_bytes(b"x"); (dense / "sparse" / "images.bin").write_bytes(b"x"); (dense / "stereo" / "consistency_graphs").mkdir(parents=True); (dense / "fused.ply").write_bytes(VALID_PLY)
            runner = FakeRunner(); report = run_pipeline(config, runner)
            self.assertEqual(report.status, "completed")
            command = next(command for command in runner.commands if command[1] == "delaunay_mesher")
            self.assertEqual(Path(command[command.index("--input_path") + 1]), dense)
            self.assertTrue((workspace / "exports").is_dir())

    def test_empty_fused_cloud_fails_before_meshing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = make_config(root); workspace = config.workspace; dense = workspace / "dense"; (workspace / "sparse" / "0").mkdir(parents=True); (workspace / "sparse" / "0" / "cameras.bin").write_bytes(b"x"); (workspace / "sparse" / "0" / "images.bin").write_bytes(b"x"); (dense / "images").mkdir(parents=True); (dense / "sparse").mkdir(parents=True); (dense / "fused.ply").write_bytes(b"")
            runner = NoFusionOutputRunner(); report = run_pipeline(config, runner)
            self.assertEqual(report.status, "failed")
            self.assertIn("missing, empty, or contains zero vertices", report.stages[-1].error)
            self.assertNotIn("poisson_mesher", [command[1] for command in runner.commands])

    def test_cancellation_prevents_later_stages(self):
        with tempfile.TemporaryDirectory() as temp:
            event = threading.Event(); event.set(); runner = FakeRunner(); report = run_pipeline(make_config(Path(temp)), runner, cancel_event=event)
            self.assertEqual(report.status, "cancelled")
            self.assertEqual(runner.commands, [])

    def test_paths_with_spaces_are_passed_as_arguments(self):
        with tempfile.TemporaryDirectory(prefix="scan with spaces ") as temp:
            runner = FakeRunner(); report = run_pipeline(make_config(Path(temp)), runner)
            self.assertEqual(report.status, "completed")
            self.assertTrue(any(" " in value for value in runner.commands[0] if isinstance(value, str)))


if __name__ == "__main__":
    unittest.main()
