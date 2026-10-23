# Automated 3D Scanning Workstation

Native Windows-first PySide6 application for local image-based photogrammetry.

## Current vertical slice

- Dark native dashboard with explicit device/tool status.
- COLMAP executable discovery and capability detection.
- NVIDIA/system diagnostics through non-destructive probes.
- Import a folder of JPG/JPEG/PNG/TIFF/BMP images into a managed scan project.
- Safe Scan (Low VRAM) as the default reconstruction profile, using COLMAP's verified `automatic_reconstructor` LOW workflow.
- Advanced Explicit mode for the staged mapper, dense reconstruction, and meshing workflow.
- Live logs, elapsed time, cancellation, and structured project metadata.
- Sparse-model coverage checks, fused-point validation, and resumable validated outputs.

Hardware capture and video extraction are not controlled by this application. COLMAP remains an external executable.

## Reconstruction profiles

New projects use **Safe Scan (Low VRAM)**. It selects incremental mapping, video-oriented input, LOW quality, conservative threading, CUDA when the detected COLMAP build supports it, and one Poisson mesh. It also checks sparse registration coverage before accepting dense results. Use Advanced Explicit when you need the existing individual stages, Delaunay, or a second mesh.

The application records the selected sparse model and available registration/point statistics in the reconstruction report. A model with very low coverage (for example, 3 of 89 images) is stopped before dense reconstruction rather than being presented as a valid scan. GPU/CUDA errors remain in the live log and report with an actionable lower-memory hint.

The default profile uses COLMAP's official `automatic_reconstructor` command only when the installed executable's help output confirms the required LOW syntax. If that syntax is unavailable, ScanForge falls back to the validated Explicit Safe Scan stages instead of guessing flags.

If automatic LOW is unavailable on a compatible installation, ScanForge automatically reports the reason and falls back to the validated Explicit Safe Scan stages. Capability results are refreshed with `ColmapAdapter.refresh()` when diagnostics are explicitly refreshed; otherwise they are reused while the executable path, version, and file metadata remain unchanged.

## Windows first-time setup

Double-click `setup.bat`, or run it from a terminal in the project directory. It:

- Creates `.venv` only when its Python interpreter does not already exist.
- Installs the project in editable mode with `python -m pip install -e .`.
- Installs the dependencies declared in `pyproject.toml` into the project environment.
- Verifies that the application, PySide6, OpenCV, NumPy, and psutil import successfully.
- Writes `.setup-complete` only after all checks pass.

The `.venv` directory persists between launches. A valid existing environment is reused and is never deleted or recreated automatically.

## One-click launch

Double-click `launch.bat`. It reuses the existing environment and starts:

```text
.venv\Scripts\python.exe -m scanner_app
```

If `.venv`, `.setup-complete`, or the import verification is missing—or if `pyproject.toml` has changed since setup—`launch.bat` runs `setup.bat` first. Application and setup errors remain visible in the terminal window.

## Updating dependencies

After changing `pyproject.toml`, run `setup.bat` again. The launcher fingerprints `pyproject.toml`, so it will also detect the change and refresh the environment automatically on the next launch. Existing scan output and the existing virtual environment are not deleted.

## Troubleshooting setup

- If Python is not found, install Python 3.11 or newer and ensure `python` works in a new terminal.
- If package installation fails, check internet access, proxy settings, and the Python version shown by `python --version`.
- If imports fail, rerun `setup.bat` and keep the terminal open to inspect the traceback.
- If setup is interrupted, no successful marker is written; rerun `setup.bat`.

## Run manually

```powershell
launch.bat
```

## COLMAP configuration

COLMAP is an external native executable and is not installed through pip. The application first checks `colmap` on PATH and also supports the current Windows fallback location `C:\Users\Parth\colmap\bin\colmap.exe`. You can provide an explicit executable path when launching:

```powershell
.venv\Scripts\python.exe -m scanner_app --colmap "C:\path with spaces\colmap.exe"
```

The application reports the detected COLMAP version and capabilities; it does not claim that a GPU is usable merely because `nvidia-smi` is present.

## Tests

```powershell
python -m unittest discover -s tests -v
```
