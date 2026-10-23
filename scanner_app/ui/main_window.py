from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from ..core.colmap import ColmapAdapter
from ..core.diagnostics import system_snapshot
from ..core.project import create_project
from ..core.turntable import Turntable


class MainWindow(QMainWindow):
    def __init__(self, colmap_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Scanner Workstation")
        self.resize(1100, 720)
        self.colmap = ColmapAdapter(colmap_path)
        self.capabilities = self.colmap.discover()
        self.project: Path | None = None
        self.process: QProcess | None = None
        self.turntable = Turntable("COM9")
        self.rotation_in_progress = False
        self.started_at = 0.0
        self._build_ui()
        self._refresh_diagnostics()

    def _build_ui(self) -> None:
        self.setStyleSheet("QMainWindow,QWidget{background:#111827;color:#e5e7eb;font-size:14px} QPushButton{background:#2563eb;border:0;border-radius:6px;padding:10px 14px} QPushButton:disabled{background:#374151;color:#9ca3af} QListWidget,QPlainTextEdit{background:#0b1220;border:1px solid #263244;border-radius:6px} QLabel#title{font-size:26px;font-weight:700} QLabel#muted{color:#9ca3af}")
        root = QWidget(); self.setCentralWidget(root); layout = QVBoxLayout(root)
        header = QHBoxLayout(); title = QLabel("Scanner Workstation"); title.setObjectName("title"); header.addWidget(title); header.addStretch()
        self.status = QLabel("Ready"); self.status.setObjectName("muted"); header.addWidget(self.status); layout.addLayout(header)
        actions = QHBoxLayout()
        self.import_button = QPushButton("Import Images"); self.import_button.clicked.connect(self.import_images); actions.addWidget(self.import_button)
        self.run_button = QPushButton("Start Sparse Reconstruction"); self.run_button.clicked.connect(self.start_reconstruction); self.run_button.setEnabled(False); actions.addWidget(self.run_button)
        self.cancel_button = QPushButton("Cancel"); self.cancel_button.clicked.connect(self.cancel); self.cancel_button.setEnabled(False); actions.addWidget(self.cancel_button); actions.addStretch(); layout.addLayout(actions)
        turntable_actions = QHBoxLayout()
        self.turntable_status = QLabel("Turntable: checking COM9…")
        turntable_actions.addWidget(self.turntable_status)
        self.rotate_button = QPushButton("Rotate once")
        self.rotate_button.clicked.connect(self.rotate_once)
        self.rotate_button.setEnabled(False)
        turntable_actions.addWidget(self.rotate_button)
        turntable_actions.addStretch()
        layout.addLayout(turntable_actions)
        self.cards = QListWidget(); self.cards.setMaximumHeight(150); layout.addWidget(self.cards)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.setVisible(False); layout.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); layout.addWidget(self.log, 1)
        self.clock = QTimer(self); self.clock.timeout.connect(self._update_elapsed); self.clock.start(1000)
        self.serial_timer = QTimer(self); self.serial_timer.timeout.connect(self._poll_turntable); self.serial_timer.start(250)

    def _refresh_diagnostics(self) -> None:
        self.cards.clear()
        c = self.capabilities
        self.cards.addItem(f"COLMAP: {c.version or 'Unavailable'} — {'CUDA build detected' if c.has_cuda_build else 'GPU capability unknown'}")
        self.cards.addItem(f"Executable: {c.executable or c.error}")
        gpu = system_snapshot().get("nvidia_smi", "Unavailable")
        self.cards.addItem(f"NVIDIA diagnostics: {gpu or 'Unavailable'}")
        self.cards.addItem("Camera: Not connected (no fabricated device state)")
        self.cards.addItem("Turntable: checking COM9")

    def _poll_turntable(self) -> None:
        connected = self.turntable.probe()
        if connected:
            self.turntable_status.setText("Turntable: connected on COM9")
            self.turntable_status.setStyleSheet("color:#34d399")
            self.rotate_button.setEnabled(not self.rotation_in_progress)
            for line in self.turntable.read_lines():
                if line.upper() == "OK" and self.rotation_in_progress:
                    self.rotation_in_progress = False
                    self.rotate_button.setEnabled(True)
                    self.status.setText("Rotation complete")
                    self.log.appendPlainText("Turntable completed one full rotation (OK)")
                elif line.upper().startswith("ERR"):
                    self.rotation_in_progress = False
                    self.rotate_button.setEnabled(True)
                    self.status.setText("Turntable error")
                    self.log.appendPlainText(f"Turntable: {line}")
        else:
            self.turntable_status.setText("Turntable: not connected (COM9)")
            self.turntable_status.setStyleSheet("color:#f87171")
            self.rotate_button.setEnabled(False)
            if self.rotation_in_progress:
                self.rotation_in_progress = False
                self.status.setText("Turntable disconnected")

    def rotate_once(self) -> None:
        try:
            self.turntable.rotate_once()
        except Exception as exc:
            self.status.setText("Turntable command failed")
            self.log.appendPlainText(f"Turntable error: {exc}")
            return
        self.rotation_in_progress = True
        self.rotate_button.setEnabled(False)
        self.status.setText("Rotating… waiting for OK")
        self.log.appendPlainText("Sent ROTATE; waiting for ESP32 OK")

    def import_images(self) -> None:
        source = QFileDialog.getExistingDirectory(self, "Choose image folder")
        if not source: return
        try:
            self.project, count = create_project(Path.cwd() / "output", Path(source))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import failed", str(exc)); return
        self.run_button.setEnabled(self.capabilities.executable is not None)
        self.status.setText(f"Imported {count} images")
        self.log.appendPlainText(f"Project: {self.project}\nImported {count} images from {source}")

    def start_reconstruction(self) -> None:
        if not self.project or not self.capabilities.executable: return
        images = self.project / "source" / "original_images"
        database = self.project / "reconstruction" / "incremental" / "database.db"
        sparse = self.project / "reconstruction" / "incremental" / "sparse"
        sparse.mkdir(parents=True, exist_ok=True)
        self.process = QProcess(self); self.process.setProgram(str(self.capabilities.executable))
        self.process.setArguments(["automatic_reconstructor", "--image_path", str(images), "--workspace_path", str(self.project / "reconstruction" / "incremental")])
        self.process.readyReadStandardOutput.connect(lambda: self.log.appendPlainText(bytes(self.process.readAllStandardOutput()).decode(errors="replace").rstrip()))
        self.process.readyReadStandardError.connect(lambda: self.log.appendPlainText(bytes(self.process.readAllStandardError()).decode(errors="replace").rstrip()))
        self.process.finished.connect(self._finished)
        self.started_at = time.monotonic(); self.progress.setVisible(True); self.cancel_button.setEnabled(True); self.run_button.setEnabled(False); self.status.setText("Running COLMAP automatic reconstruction")
        self.log.appendPlainText("Starting COLMAP; progress may be indeterminate for native stages.")
        self.process.start()

    def cancel(self) -> None:
        if self.process and self.process.state() != QProcess.NotRunning:
            self.status.setText("Cancelling…"); self.process.terminate()

    def _finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        elapsed = time.monotonic() - self.started_at
        cancelled = self.status.text().startswith("Cancelling")
        self.status.setText(("Cancelled" if cancelled else "Completed" if code == 0 else "Failed") + f" after {elapsed:.1f}s")
        self.log.appendPlainText(f"COLMAP exit code: {code}")
        self.progress.setVisible(False); self.cancel_button.setEnabled(False); self.run_button.setEnabled(self.project is not None and self.capabilities.executable is not None); self.process = None

    def _update_elapsed(self) -> None:
        if self.process and self.process.state() != QProcess.NotRunning:
            self.status.setToolTip(f"Elapsed: {time.monotonic() - self.started_at:.1f}s")
