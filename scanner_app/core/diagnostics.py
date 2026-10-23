from __future__ import annotations

import json
import platform
import shutil
import subprocess


def system_snapshot() -> dict[str, object]:
    snapshot: dict[str, object] = {"platform": platform.platform(), "python": platform.python_version()}
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            result = subprocess.run([nvidia, "--query-gpu=name,driver_version,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            snapshot["nvidia_smi"] = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
        except subprocess.SubprocessError as exc:
            snapshot["nvidia_smi"] = str(exc)
    else:
        snapshot["nvidia_smi"] = "Unavailable"
    return snapshot
