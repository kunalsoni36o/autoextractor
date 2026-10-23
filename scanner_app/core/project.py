from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def create_project(root: Path, source: Path) -> tuple[Path, int]:
    project = root / f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    (project / "source" / "original_images").mkdir(parents=True)
    (project / "reconstruction" / "incremental").mkdir(parents=True)
    (project / "logs").mkdir()
    files = [p for p in source.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED]
    if not files:
        raise ValueError("The selected folder contains no supported images.")
    for image in files:
        shutil.copy2(image, project / "source" / "original_images" / image.name)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "image_count": len(files),
        "images": [p.name for p in files],
    }
    (project / "project.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return project, len(files)
