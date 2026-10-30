from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def find_images(source: Path) -> list[Path]:
    return sorted((p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED), key=lambda p: str(p).lower())


def create_project(root: Path, source: Path) -> tuple[Path, int]:
    project = root / f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    (project / "source" / "original_images").mkdir(parents=True)
    (project / "reconstruction" / "incremental").mkdir(parents=True)
    (project / "logs").mkdir()
    files = find_images(source)
    if not files:
        raise ValueError("The selected folder contains no supported images.")
    destination_root = project / "source" / "original_images"
    for image in files:
        destination = destination_root / image.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image, destination)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "image_count": len(files),
        "images": [p.name for p in files],
    }
    (project / "project.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return project, len(files)
