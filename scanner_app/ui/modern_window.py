from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QComboBox, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpacerItem, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from ..core.colmap import ColmapAdapter
from ..core.diagnostics import system_snapshot
from ..core.project import SUPPORTED, create_project
from ..core.reconstruction import PipelineConfig, ReconstructionWorker
from ..core.profiles import SAFE_SCAN
from ..core.turntable import Turntable

LIGHT = {"background": "#F4F8F5", "surface": "#FFFFFF", "surface_alt": "#E6F3EA", "primary": "#176B50", "primary_hover": "#21865E", "text": "#183D2F", "muted": "#668073", "border": "#DFE9E1", "danger": "#B94A48", "warning": "#A66A1F"}
DARK = {"background": "#101C17", "surface": "#172820", "surface_alt": "#20382C", "primary": "#5BC795", "primary_hover": "#76D8AA", "text": "#E8F4EC", "muted": "#A0B8AA", "border": "#2A4738", "danger": "#F08A86", "warning": "#E3AE5C"}


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")


class StatCard(Card):
    def __init__(self, label: str, value: str = "Unavailable", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)
        layout.addWidget(QLabel(label.upper(), objectName="eyebrow"))
        self.value = QLabel(value, objectName="statValue")
        self.value.setWordWrap(True)
        layout.addWidget(self.value)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


class StatusBadge(QLabel):
    def __init__(self, text: str = "READY", tone: str = "neutral") -> None:
        super().__init__(text)
        self.setObjectName(f"badge_{tone}")
        self.setAlignment(Qt.AlignCenter)


class SidebarButton(QPushButton):
    selected = Signal()

    def __init__(self, icon: str, text: str, enabled: bool = True) -> None:
        super().__init__(f"  {icon}  {text}")
        self.setCheckable(True)
        self.setEnabled(enabled)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(42)
        self.setProperty("nav", True)


class Backdrop(QWidget):
    """Paints the workspace background and a restrained isometric cube motif."""

    def __init__(self, palette: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_colors = palette

    def set_palette(self, palette: dict[str, str]) -> None:
        self.palette_colors = palette
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(self.palette_colors["background"]))
        width, height = self.width(), self.height()
        size = min(width, height) * 0.23
        cx, cy = width * 0.82, height * 0.70
        top = QPolygonF([
            QPointF(cx, cy - size), QPointF(cx + size, cy - size * 0.48),
            QPointF(cx, cy), QPointF(cx - size, cy - size * 0.48),
        ])
        left = QPolygonF([
            QPointF(cx - size, cy - size * 0.48), QPointF(cx, cy),
            QPointF(cx, cy + size * 0.98), QPointF(cx - size, cy + size * 0.50),
        ])
        right = QPolygonF([
            QPointF(cx, cy), QPointF(cx + size, cy - size * 0.48),
            QPointF(cx + size, cy + size * 0.50), QPointF(cx, cy + size * 0.98),
        ])
        alpha = 46 if self.palette_colors is LIGHT else 34
        for polygon, color in ((top, "#7BC9A1"), (left, "#3B9B70"), (right, "#176B50")):
            fill = color
            painter.setOpacity(alpha / 255)
            painter.setBrush(QColor(fill))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(polygon)
        painter.setOpacity(1.0)
        painter.setPen(QColor(self.palette_colors["border"]))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(top)
        painter.drawPolygon(left)
        painter.drawPolygon(right)


class MainWindow(QMainWindow):
    def __init__(self, colmap_path: Path | None = None) -> None:
        super().__init__()
        self.settings = QSettings("ScannerWorkstation", "ScannerWorkstation")
        self.dark_mode = self.settings.value("dark_mode", False, type=bool)
        self.colmap = ColmapAdapter(colmap_path)
        self.capabilities = self.colmap.discover()
        self.project: Path | None = None
        self.output_root = Path(self.settings.value("output_root", str(Path.cwd() / "output")))
        self.process: QProcess | None = None  # kept for compatibility with older UI integrations
        self.worker: ReconstructionWorker | None = None
        self.started_at = 0.0
        self.turntable = Turntable("COM9")
        self.rotation_in_progress = False
        self.sidebar_buttons: list[SidebarButton] = []
        self._build_ui()
        self._apply_theme()
        self._refresh_diagnostics()
        self._restore_window()

    def _build_ui(self) -> None:
        self.setWindowTitle("Scanner Workstation")
        self.setMinimumSize(980, 650)
        root = Backdrop(DARK if self.dark_mode else LIGHT)
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(236)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 18)
        side.setSpacing(8)
        side.addWidget(QLabel("◈  SCANNER\n    WORKSTATION", objectName="brand"))
        side.addSpacing(22)
        for icon, label, page in [("⌂", "Overview", 0), ("＋", "New Scan", 1), ("◌", "Reconstruction", 2), ("▣", "Hardware & GPU", 3), ("⚙", "Settings", 4)]:
            button = SidebarButton(icon, label)
            button.clicked.connect(lambda _checked=False, p=page: self._navigate(p))
            self.sidebar_buttons.append(button)
            side.addWidget(button)
        side.addSpacing(12)
        side.addWidget(QLabel("PLANNED MODULES", objectName="eyebrow"))
        for label in ("Image Review", "Results & Export"):
            button = SidebarButton("·", label, enabled=False)
            button.setToolTip("This workflow stage is not implemented yet.")
            side.addWidget(button)
        side.addItem(QSpacerItem(10, 10, QSizePolicy.Minimum, QSizePolicy.Expanding))
        self.sidebar_status = QLabel("LOCAL WORKSTATION\nNo data leaves this machine", objectName="sidebarFootnote")
        side.addWidget(self.sidebar_status)
        shell.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(34, 28, 34, 26)
        content_layout.setSpacing(18)
        top = QHBoxLayout()
        self.page_title = QLabel("Overview", objectName="pageTitle")
        top.addWidget(self.page_title)
        top.addStretch()
        self.theme_button = QToolButton()
        self.theme_button.setText("☼")
        self.theme_button.setToolTip("Toggle light/dark mode")
        self.theme_button.setCheckable(True)
        self.theme_button.setChecked(self.dark_mode)
        self.theme_button.clicked.connect(self._toggle_theme)
        top.addWidget(self.theme_button)
        self.global_status = StatusBadge()
        top.addWidget(self.global_status)
        content_layout.addLayout(top)
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages, 1)
        self._build_overview()
        self._build_new_scan()
        self._build_reconstruction()
        self._build_hardware()
        self._build_settings()
        shell.addWidget(content, 1)
        self._navigate(0)

        self.clock = QTimer(self)
        self.clock.timeout.connect(self._update_elapsed)
        self.clock.start(1000)
        self.resource_timer = QTimer(self)
        self.resource_timer.timeout.connect(self._refresh_diagnostics)
        self.resource_timer.start(5000)
        self.turntable_timer = QTimer(self)
        self.turntable_timer.timeout.connect(self._poll_turntable)
        self.turntable_timer.start(250)

    def _page(self) -> QVBoxLayout:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 8, 8)
        layout.setSpacing(18)
        scroll.setWidget(body)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.pages.addWidget(page)
        return layout

    def _build_overview(self) -> None:
        layout = self._page()
        intro = QHBoxLayout()
        copy = QVBoxLayout()
        copy.addWidget(QLabel("Your scanning workspace", objectName="sectionTitle"))
        sub = QLabel("Import a folder of images, review the project, then run a real COLMAP reconstruction.", objectName="muted")
        sub.setWordWrap(True)
        copy.addWidget(sub)
        intro.addLayout(copy)
        intro.addStretch()
        self.new_scan_button = QPushButton("＋  New scan")
        self.new_scan_button.clicked.connect(lambda: self._navigate(1))
        intro.addWidget(self.new_scan_button)
        layout.addLayout(intro)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.image_stat = StatCard("Imported images")
        self.project_stat = StatCard("Active project")
        self.colmap_stat = StatCard("COLMAP")
        self.gpu_stat = StatCard("GPU diagnostics")
        for index, card in enumerate((self.image_stat, self.project_stat, self.colmap_stat, self.gpu_stat)):
            grid.addWidget(card, 0, index)
        layout.addLayout(grid)
        empty = Card()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(28, 30, 28, 30)
        empty_layout.setSpacing(10)
        empty_layout.addWidget(QLabel("Start with an image folder", objectName="sectionTitle"))
        empty_layout.addWidget(QLabel("No project is active yet. Choose New scan to create a managed project without changing your original images.", objectName="muted"))
        self.overview_import = QPushButton("Import images")
        self.overview_import.clicked.connect(self.import_images)
        empty_layout.addWidget(self.overview_import, 0, Qt.AlignLeft)
        layout.addWidget(empty)
        run_card = Card()
        run_layout = QHBoxLayout(run_card)
        run_layout.setContentsMargins(20, 16, 20, 16)
        self.overview_job = QLabel("No reconstruction running", objectName="cardTitle")
        run_layout.addWidget(self.overview_job)
        run_layout.addStretch()
        self.overview_run = QPushButton("Start reconstruction")
        self.overview_run.clicked.connect(self.start_reconstruction)
        self.overview_run.setEnabled(False)
        run_layout.addWidget(self.overview_run)
        layout.addWidget(run_card)
        layout.addStretch()

    def _build_new_scan(self) -> None:
        layout = self._page()
        layout.addWidget(QLabel("New scan", objectName="sectionTitle"))
        layout.addWidget(self._hint("Create a managed project from existing photographs. Original files are copied, never modified."))
        card = Card()
        form = QFormLayout(card)
        form.setContentsMargins(24, 24, 24, 24)
        form.setSpacing(16)
        self.source_path = QLabel("No folder selected", objectName="pathValue")
        self.source_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        source_button = QPushButton("Choose image folder")
        source_button.clicked.connect(self.choose_source)
        form.addRow("Input images", self._row(self.source_path, source_button))
        self.output_path = QLabel(str(self.output_root), objectName="pathValue")
        self.output_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        output_button = QPushButton("Choose output folder")
        output_button.clicked.connect(self.choose_output)
        form.addRow("Project location", self._row(self.output_path, output_button))
        self.image_summary = QLabel("Supported formats: JPG, JPEG, PNG, TIFF, BMP", objectName="muted")
        form.addRow("Validation", self.image_summary)
        layout.addWidget(card)
        confirm = Card()
        confirm_layout = QVBoxLayout(confirm)
        confirm_layout.setContentsMargins(24, 20, 24, 20)
        confirm_layout.setSpacing(8)
        confirm_layout.addWidget(QLabel("Ready to create?", objectName="cardTitle"))
        self.confirmation = QLabel("Select an image folder to continue.", objectName="muted")
        self.confirmation.setWordWrap(True)
        confirm_layout.addWidget(self.confirmation)
        self.create_button = QPushButton("Create project from images")
        self.create_button.clicked.connect(self.import_images)
        self.create_button.setEnabled(False)
        confirm_layout.addWidget(self.create_button, 0, Qt.AlignLeft)
        layout.addWidget(confirm)
        layout.addStretch()

    def _build_reconstruction(self) -> None:
        layout = self._page()
        layout.addWidget(QLabel("Reconstruction", objectName="sectionTitle"))
        layout.addWidget(self._hint("Safe Scan (Low VRAM) is the default. It uses COLMAP's verified automatic profile and requires enough registered images before accepting a result. Advanced mode exposes the staged controls."))
        card = Card()
        form = QFormLayout(card)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(14)
        self.recon_project = QLabel("No active project", objectName="pathValue")
        form.addRow("Project", self.recon_project)
        self.stage_label = QLabel("Idle")
        form.addRow("Current stage", self.stage_label)
        self.process_label = QLabel("Waiting for a project")
        form.addRow("Process", self.process_label)
        self.profile_select = QComboBox(); self.profile_select.addItems([SAFE_SCAN.name, "Advanced Explicit"])
        form.addRow("Reconstruction profile", self.profile_select)
        self.mapper_select = QComboBox(); self.mapper_select.addItems(["Incremental", "Global"]); form.addRow("Sparse mapper", self.mapper_select)
        self.mesher_select = QComboBox(); self.mesher_select.addItems(["Poisson", "Delaunay"]); form.addRow("Mesher", self.mesher_select)
        self.second_mesh_check = QCheckBox("Also generate the other mesh")
        form.addRow("Comparison", self.second_mesh_check)
        self.profile_select.currentIndexChanged.connect(self._profile_changed)
        self._profile_changed(self.profile_select.currentIndex())
        layout.addWidget(card)
        self.stage_names = ["Validate input", "Prepare workspace", "Automatic reconstruction"]
        stages_card = Card()
        stages_layout = QVBoxLayout(stages_card)
        stages_layout.setContentsMargins(20, 16, 20, 16)
        stages_layout.addWidget(QLabel("Pipeline stages", objectName="cardTitle"))
        self.stage_list = QListWidget()
        self.stage_list.setMaximumHeight(190)
        stages_layout.addWidget(self.stage_list)
        layout.addWidget(stages_card)
        self._reset_stage_list()
        progress_card = Card()
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(24, 20, 24, 20)
        progress_layout.setSpacing(12)
        row = QHBoxLayout()
        self.progress_title = QLabel("Ready to reconstruct", objectName="cardTitle")
        row.addWidget(self.progress_title)
        row.addStretch()
        self.elapsed_label = QLabel("Elapsed —", objectName="muted")
        row.addWidget(self.elapsed_label)
        progress_layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        progress_layout.addWidget(self.progress)
        self.recon_start = QPushButton("Start sparse reconstruction")
        self.recon_start.clicked.connect(self.start_reconstruction)
        self.recon_start.setEnabled(False)
        self.recon_cancel = QPushButton("Cancel operation")
        self.recon_cancel.clicked.connect(self.cancel)
        self.recon_cancel.setEnabled(False)
        progress_layout.addWidget(self.recon_start, 0, Qt.AlignLeft)
        progress_layout.addWidget(self.recon_cancel, 0, Qt.AlignLeft)
        self.result_summary = QLabel("No reconstruction outputs yet.", objectName="muted")
        self.result_summary.setWordWrap(True)
        progress_layout.addWidget(self.result_summary)
        layout.addWidget(progress_card)
        logs = Card()
        log_layout = QVBoxLayout(logs)
        log_layout.setContentsMargins(20, 16, 20, 16)
        header = QHBoxLayout()
        header.addWidget(QLabel("Live logs", objectName="cardTitle"))
        header.addStretch()
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.log.toPlainText()))
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.log.clear())
        header.addWidget(copy)
        header.addWidget(clear)
        log_layout.addLayout(header)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log.setFont(QFont("Cascadia Mono", 10))
        log_layout.addWidget(self.log)
        layout.addWidget(logs)
        layout.addStretch()

    def _profile_changed(self, index: int) -> None:
        advanced = index == 1
        self.mapper_select.setEnabled(advanced)
        self.mesher_select.setEnabled(advanced)
        self.second_mesh_check.setEnabled(advanced)

    def _build_hardware(self) -> None:
        layout = self._page()
        layout.addWidget(QLabel("Hardware & GPU", objectName="sectionTitle"))
        layout.addWidget(self._hint("The turntable is detected and tested through COM9. The scanning camera will be added later."))
        grid = QGridLayout()
        grid.setSpacing(12)
        self.hardware_colmap = StatCard("COLMAP")
        self.hardware_gpu = StatCard("GPU")
        self.hardware_driver = StatCard("Driver / CUDA")
        self.hardware_camera = StatCard("Camera")
        self.hardware_turntable = StatCard("Turntable")
        for index, card in enumerate((self.hardware_colmap, self.hardware_gpu, self.hardware_driver, self.hardware_camera, self.hardware_turntable)):
            grid.addWidget(card, index // 3, index % 3)
        layout.addLayout(grid)
        details = Card()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(20, 18, 20, 18)
        details_layout.addWidget(QLabel("Raw NVIDIA diagnostic", objectName="cardTitle"))
        self.gpu_details = QPlainTextEdit()
        self.gpu_details.setReadOnly(True)
        self.gpu_details.setMaximumHeight(120)
        details_layout.addWidget(self.gpu_details)
        layout.addWidget(details)
        tests = Card()
        tests_layout = QVBoxLayout(tests)
        tests_layout.setContentsMargins(20, 18, 20, 18)
        tests_layout.addWidget(QLabel("Hardware testing", objectName="cardTitle"))
        tests_layout.addWidget(self._hint("Command one complete physical turntable rotation. The ESP32 must send OK only after it has completed the rotation."))
        test_row = QHBoxLayout()
        self.rotate_test_button = QPushButton("Rotate once")
        self.rotate_test_button.clicked.connect(self.rotate_once)
        self.rotate_test_button.setEnabled(False)
        test_row.addWidget(self.rotate_test_button)
        test_row.addStretch()
        tests_layout.addLayout(test_row)
        self.hardware_test_status = QLabel("No hardware test run yet.", objectName="muted")
        self.hardware_test_status.setWordWrap(True)
        tests_layout.addWidget(self.hardware_test_status)
        layout.addWidget(tests)
        layout.addStretch()

    def _build_settings(self) -> None:
        layout = self._page()
        layout.addWidget(QLabel("Settings", objectName="sectionTitle"))
        card = Card()
        form = QFormLayout(card)
        form.setContentsMargins(24, 24, 24, 24)
        form.setSpacing(18)
        self.settings_theme = QCheckBox("Use dark mode")
        self.settings_theme.setChecked(self.dark_mode)
        self.settings_theme.clicked.connect(self._set_theme)
        form.addRow("Appearance", self.settings_theme)
        self.settings_logs = QCheckBox("Show logs automatically during reconstruction")
        self.settings_logs.setChecked(self.settings.value("show_logs", True, type=bool))
        self.settings_logs.clicked.connect(lambda checked: self.settings.setValue("show_logs", checked))
        form.addRow("Diagnostics", self.settings_logs)
        colmap_info = QLabel(str(self.capabilities.executable or "Not detected"), objectName="pathValue")
        colmap_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("COLMAP executable", colmap_info)
        layout.addWidget(card)
        layout.addWidget(self._hint("COLMAP is detected from PATH and the configured local fallback. Use the launcher with --colmap for another executable."))
        layout.addStretch()

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text, objectName="muted")
        label.setWordWrap(True)
        return label

    def _row(self, value: QWidget, action: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(value, 1)
        layout.addWidget(action)
        return row

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.page_title.setText(["Overview", "New scan", "Reconstruction", "Hardware & GPU", "Settings"][index])
        for i, button in enumerate(self.sidebar_buttons):
            button.setChecked(i == index)

    def _toggle_theme(self) -> None:
        self._set_theme(self.theme_button.isChecked())

    def _set_theme(self, enabled: bool) -> None:
        self.dark_mode = enabled
        self.theme_button.setChecked(enabled)
        self.settings_theme.setChecked(self.dark_mode)
        self.settings.setValue("dark_mode", self.dark_mode)
        self._apply_theme()

    def _apply_theme(self) -> None:
        c = DARK if self.dark_mode else LIGHT
        self.centralWidget().set_palette(c)  # type: ignore[attr-defined]
        self.setStyleSheet(f"""QWidget{{background:transparent;color:{c['text']};font-size:14px}} QMainWindow{{background:{c['background']};color:{c['text']}}} QWidget#root{{background:transparent}} QWidget#content{{background:transparent}} QScrollArea,QScrollArea>QWidget>QWidget{{background:transparent}} QFrame#sidebar{{background:{c['surface']};border-right:1px solid {c['border']}}} QLabel#brand{{color:{c['primary']};font-size:15px;font-weight:800}} QLabel#pageTitle{{color:{c['text']};font-size:28px;font-weight:750}} QLabel#sectionTitle{{color:{c['text']};font-size:21px;font-weight:700}} QLabel#cardTitle{{color:{c['text']};font-size:15px;font-weight:700}} QLabel#eyebrow{{color:{c['muted']};font-size:10px;font-weight:800;letter-spacing:1px}} QLabel#muted,QLabel#sidebarFootnote{{color:{c['muted']}}} QLabel#statValue{{color:{c['text']};font-size:18px;font-weight:700}} QLabel#pathValue{{color:{c['text']}}} QFrame#card{{background:{c['surface']};border:1px solid {c['border']};border-radius:12px}} QFrame#card QWidget{{background:transparent}} QPushButton{{color:{c['text']};background:{c['surface']};border:1px solid {c['border']};border-radius:7px;padding:9px 14px;min-height:18px}} QPushButton:hover{{border-color:{c['primary']};color:{c['primary']}}} QPushButton:pressed{{background:{c['surface_alt']}}} QPushButton:disabled{{color:{c['muted']};background:{c['background']}}} QPushButton[nav="true"]{{text-align:left;border:0;background:transparent;color:{c['muted']};padding:10px 12px}} QPushButton[nav="true"]:checked{{color:{c['primary']};background:{c['surface_alt']};font-weight:700;border-left:3px solid {c['primary']}}} QPushButton#primary{{color:#FFFFFF;background:{c['primary']};border:0;font-weight:700}} QPushButton#primary:hover{{background:{c['primary_hover']}}} QProgressBar{{background:{c['surface_alt']};border:0;border-radius:5px;min-height:9px}} QProgressBar::chunk{{background:{c['primary']};border-radius:5px}} QPlainTextEdit{{background:{c['surface_alt']};color:{c['text']};border:1px solid {c['border']};border-radius:8px;padding:8px}} QScrollArea{{border:0;background:transparent}} QToolButton{{color:{c['primary']};background:{c['surface_alt']};border:0;border-radius:16px;padding:7px 10px}} QLabel#badge_success,QLabel#badge_neutral{{color:{c['primary']};background:{c['surface_alt']};border-radius:12px;padding:6px 12px;font-size:11px;font-weight:800}} QLabel#badge_warning{{color:{c['warning']};background:#FFF4DC;border-radius:12px;padding:6px 12px;font-size:11px;font-weight:800}} QLabel#badge_danger{{color:{c['danger']};background:#FDE9E7;border-radius:12px;padding:6px 12px;font-size:11px;font-weight:800}}""")
        for button in (self.new_scan_button, self.overview_import, self.overview_run, self.recon_start):
            button.setObjectName("primary")

    def _refresh_diagnostics(self) -> None:
        version = self.capabilities.version or "Unavailable"
        self.colmap_stat.set_value(version)
        self.hardware_colmap.set_value(version)
        raw = str(system_snapshot().get("nvidia_smi", "Unavailable"))
        line = raw.splitlines()[0] if raw and raw != "Unavailable" else "Unavailable"
        gpu = line.split(",")[0].strip() if line != "Unavailable" else "Unavailable"
        self.gpu_stat.set_value(gpu or "Unavailable")
        self.hardware_gpu.set_value(gpu or "Unavailable")
        self.gpu_details.setPlainText(raw)
        self.hardware_driver.set_value("CUDA build detected" if self.capabilities.has_cuda_build else "Unavailable")
        self.hardware_camera.set_value("Will be added later")
        turntable_connected = self.turntable.probe()
        self.hardware_turntable.set_value("Connected on COM9" if turntable_connected else "Not connected")
        self.rotate_test_button.setEnabled(turntable_connected and not self.rotation_in_progress)
        if not self.project:
            self.project_stat.set_value("None selected")

    def rotate_once(self) -> None:
        if self.rotation_in_progress:
            return
        try:
            self.turntable.rotate_once()
        except Exception as exc:
            self.hardware_test_status.setText(f"Turntable test failed: {exc}")
            return
        self.rotation_in_progress = True
        self.rotate_test_button.setEnabled(False)
        self.hardware_test_status.setText("Rotation command sent. Waiting for ESP32 OK…")

    def _poll_turntable(self) -> None:
        connected = self.turntable.probe()
        self.hardware_turntable.set_value("Connected on COM9" if connected else "Not connected")
        self.rotate_test_button.setEnabled(connected and not self.rotation_in_progress)
        for line in self.turntable.read_lines():
            if line.upper() == "OK" and self.rotation_in_progress:
                self.rotation_in_progress = False
                self.rotate_test_button.setEnabled(True)
                self.hardware_test_status.setText("Turntable test passed: one rotation completed and OK was received.")
            elif line.upper().startswith("ERR") and self.rotation_in_progress:
                self.rotation_in_progress = False
                self.rotate_test_button.setEnabled(True)
                self.hardware_test_status.setText(f"Turntable test failed: ESP32 reported {line}.")

    def choose_source(self) -> None:
        source = QFileDialog.getExistingDirectory(self, "Choose image folder")
        if not source:
            return
        path = Path(source)
        files = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED]
        self.selected_source = path
        self.source_path.setText(str(path))
        self.image_summary.setText(f"{len(files)} supported image(s) found")
        self.create_button.setEnabled(bool(files))
        self.confirmation.setText(f"A new scan project will copy {len(files)} supported image(s) into:\n{self.output_root}")

    def choose_output(self) -> None:
        output = QFileDialog.getExistingDirectory(self, "Choose project output folder", str(self.output_root))
        if output:
            self.output_root = Path(output)
            self.output_path.setText(str(self.output_root))
            self.settings.setValue("output_root", str(self.output_root))

    def import_images(self) -> None:
        if not hasattr(self, "selected_source"):
            self.choose_source()
        if not hasattr(self, "selected_source"):
            return
        try:
            self.project, count = create_project(self.output_root, self.selected_source)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self.run_button_state(True)
        self.status_message(f"Imported {count} images")
        self.log_message(f"Project created: {self.project}\nImported {count} images from {self.selected_source}")
        self._navigate(0)

    def run_button_state(self, enabled: bool) -> None:
        for button in (self.overview_run, self.recon_start):
            button.setEnabled(enabled and self.capabilities.executable is not None)
        self.recon_project.setText(str(self.project) if self.project else "No active project")
        self.project_stat.set_value(self.project.name if self.project else "None selected")
        self.image_stat.set_value(self._project_image_count() if self.project else "None selected")

    def _project_image_count(self) -> str:
        return str(len(list((self.project / "source" / "original_images").glob("*")))) if self.project else "Unavailable"

    def _reset_stage_list(self) -> None:
        self.stage_list.clear()
        for name in self.stage_names:
            self.stage_list.addItem(f"○  Pending   {name}")

    def _on_stage(self, name: str, status: str) -> None:
        if name not in self.stage_names:
            return
        index = self.stage_names.index(name)
        symbols = {"pending": "○", "running": "●", "completed": "✓", "skipped": "—", "failed": "!", "cancelled": "×"}
        self.stage_list.item(index).setText(f"{symbols.get(status, '○')}  {status.title():<9} {name}")
        if status == "running":
            self.stage_label.setText(name)
            self.process_label.setText("COLMAP process running" if name not in {"Validate input", "Prepare workspace"} else "Preparing workflow")

    def start_reconstruction(self) -> None:
        if not self.project or not self.capabilities.executable or (self.worker and self.worker.isRunning()):
            return
        images = self.project / "source" / "original_images"
        workspace = self.project / "reconstruction" / "full"
        safe_scan = self.profile_select.currentIndex() == 0
        mapper = self.mapper_select.currentText().lower()
        mesher = self.mesher_select.currentText().lower()
        second_mesh = self.second_mesh_check.isChecked()
        if safe_scan:
            automatic_ok = self.capabilities.supports_command("automatic_reconstructor") and self.capabilities.supports_choice("--quality", "low")
            if automatic_ok:
                self.stage_names = ["Validate input", "Prepare workspace", "Automatic reconstruction"]
                profile_name = "safe_scan"
            else:
                self.stage_names = ["Validate input", "Prepare workspace", "Extract features", "Match features", "Sparse reconstruction", "Validate sparse model", "Undistort images", "PatchMatch Stereo", "Fuse depth maps", "Poisson meshing"]
                profile_name = "explicit_safe"
            config = PipelineConfig(self.capabilities.executable, images, workspace, mapper=SAFE_SCAN.mapper, mesher=SAFE_SCAN.mesher, use_gpu=self.capabilities.has_cuda_build and SAFE_SCAN.use_gpu, profile=profile_name, profile_settings=SAFE_SCAN, min_registered_ratio=SAFE_SCAN.min_registered_ratio)
        else:
            self.stage_names = ["Validate input", "Prepare workspace", "Extract features", "Match features", "Sparse reconstruction", "Validate sparse model", "Undistort images", "PatchMatch Stereo", "Fuse depth maps", f"{mesher.title()} meshing"]
            if second_mesh:
                self.stage_names.append(f"{'Delaunay' if mesher == 'poisson' else 'Poisson'} meshing")
            config = PipelineConfig(self.capabilities.executable, images, workspace, mapper=mapper, mesher=mesher, second_mesh=second_mesh, use_gpu=self.capabilities.has_cuda_build)
        self.worker = ReconstructionWorker(config, capabilities=self.capabilities, resume=True)
        self.worker.stage_changed.connect(self._on_stage)
        self.worker.output_received.connect(self.log_message)
        self.worker.completed.connect(self._finished)
        self.started_at = time.monotonic()
        self.progress.setVisible(True)
        self.recon_cancel.setEnabled(True)
        self.recon_start.setEnabled(False)
        self.overview_run.setEnabled(False)
        self._reset_stage_list()
        self.stage_label.setText("Starting")
        self.process_label.setText("Preparing Safe Scan profile" if safe_scan else "Preparing validated pipeline")
        self.progress_title.setText("Reconstruction in progress")
        self.global_status.setText("RUNNING")
        self.result_summary.setText("Running staged reconstruction; progress is reported per operation.")
        self.log_message(f"Starting full reconstruction in {workspace}")
        self.worker.start()
        self._navigate(2)

    def _append_process_output(self, error: bool) -> None:
        if not self.process:
            return
        stream = self.process.readAllStandardError() if error else self.process.readAllStandardOutput()
        text = bytes(stream).decode(errors="replace").rstrip()
        if text:
            self.log_message(text)

    def cancel(self) -> None:
        if self.worker and self.worker.isRunning():
            self.process_label.setText("Cancellation requested")
            self.stage_label.setText("Cancelling")
            self.global_status.setText("CANCELLING")
            self.worker.cancel()

    def _finished(self, report) -> None:
        elapsed = time.monotonic() - self.started_at
        result = report.status.title()
        self.status_message(f"{result} after {elapsed:.1f}s")
        self.log_message(f"Pipeline status: {report.status}; report: {report.report_path}")
        self.progress.setVisible(False)
        self.recon_cancel.setEnabled(False)
        self.worker = None
        self.stage_label.setText(result)
        self.process_label.setText("Process finished")
        self.progress_title.setText(f"{result} reconstruction")
        self.global_status.setText(result.upper())
        self.run_button_state(self.project is not None)
        artifact_text = "\n".join(report.artifacts) if report.artifacts else "No validated artifacts were produced."
        self.result_summary.setText(f"Report: {report.report_path}\nValidated artifacts:\n{artifact_text}")

    def log_message(self, message: str) -> None:
        if hasattr(self, "log"):
            self.log.appendPlainText(message)

    def status_message(self, text: str) -> None:
        self.sidebar_status.setText(f"LOCAL WORKSTATION\n{text}")

    def _update_elapsed(self) -> None:
        if self.worker and self.worker.isRunning():
            self.elapsed_label.setText(f"Elapsed {time.monotonic() - self.started_at:.1f}s")

    def _restore_window(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1240, 820)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(self, "Reconstruction running", "A reconstruction is still running. Cancel it before closing?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.No:
                event.ignore()
                return
            self.worker.cancel()
            if not self.worker.wait(5000):
                QMessageBox.warning(self, "Still running", "The native process has not stopped yet. Keep the application open until cancellation completes.")
                event.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        self.turntable.disconnect()
        super().closeEvent(event)
