from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.modern_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated 3D Scanning Workstation")
    parser.add_argument("--colmap", type=Path, help="Path to the COLMAP executable")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("Scanner Workstation")
    window = MainWindow(colmap_path=args.colmap)
    window.show()
    return app.exec()
