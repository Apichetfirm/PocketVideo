"""PySide6 / Qt 6 Modern Desktop Interface for PocketVideo.

Implements:
- Header with application name, quick language toggle (Thai default / English), and Dark/Light theme toggle
- Main Dashboard:
  - Drag-and-drop video zone (`DragDropVideoZone`) + Add Files + Add Folder
  - Selected media inspection summary (resolution, FPS, VFR, HDR10/Dolby Vision, Android metadata, efficiency warnings)
  - Five visually distinct Compression Preset cards (Presets A–E) with Thai & English titles, badges, and descriptions
  - Custom Target File Size input (MB / GB) with live two-pass bitrate & quality-risk calculator
  - Before/After Frame Comparison & Short Sample Compression dialog
  - Output container selector, Android compatibility mode selector, separate destination folder selector, and prominent Start button
  - Expandable Advanced Settings panel (Codec, Encoder, CRF, Bitrate, Resolution, FPS, Speed Preset, Audio, Two-Pass, Resource Controls)
- Secondary Views:
  - Convert View (clearly distinguishing Fast Lossless Smart Remux vs Full Transcoding)
  - Processing Queue View (per-file & overall progress, FPS, speed, elapsed, ETA, Pause/Resume, Cancel, Cancel All, Retry Failed)
  - History View (actual completed file size comparisons, space saved, percentage reduction)
  - Settings & Resource Management View (Light / Balanced / Maximum modes + Windows Job Object CPU Hard Cap %)
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import tempfile
import threading
from typing import Optional

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt, Signal
    PYSIDE6_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback when imported in headless environments without Qt
    PYSIDE6_AVAILABLE = False
    QtCore = None  # type: ignore[assignment]
    QtGui = None  # type: ignore[assignment]
    QtWidgets = None  # type: ignore[assignment]
    Qt = None  # type: ignore[assignment]
    Signal = None  # type: ignore[assignment]

from pocketvideo.core.i18n import format_bytes, format_duration
from pocketvideo.core.models import (
    CompatibilityMode,
    JobResultComparison,
    JobStatus,
    OperationMode,
    PresetId,
    ProcessPriority,
    ResourceMode,
)
from pocketvideo.media.presets import PRESET_CATALOG
from pocketvideo.media.encoder import extract_representative_frame
from pocketvideo.queue.manager import QueueItem, SUPPORTED_VIDEO_EXTENSIONS
from pocketvideo.ui.controller import AppController
from pocketvideo.ui.theme import build_stylesheet


if PYSIDE6_AVAILABLE:

    class UiBridgeSignals(QtCore.QObject):
        """Thread-safe Qt signal bridge from background QueueManager threads to the GUI thread."""
        job_updated = Signal(object)
        queue_summary = Signal(float, int, int)
        job_completed = Signal(object)
        preview_ready = Signal(str, str)

    class DragDropVideoZone(QtWidgets.QFrame):
        """Large drag-and-drop area accepting multiple video files or folders."""
        files_dropped = Signal(list)

        def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
            super().__init__(parent)
            self.setObjectName("DropZoneFrame")
            self.setAcceptDrops(True)
            self.setMinimumHeight(135)

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(20, 18, 20, 18)
            layout.setSpacing(8)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            self.title_label = QtWidgets.QLabel()
            self.title_label.setObjectName("HeadingLabel")
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            self.subtitle_label = QtWidgets.QLabel()
            self.subtitle_label.setObjectName("SubheadingLabel")
            self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            btn_row = QtWidgets.QHBoxLayout()
            btn_row.setSpacing(10)
            self.btn_add_files = QtWidgets.QPushButton()
            self.btn_add_folder = QtWidgets.QPushButton()
            self.btn_clear = QtWidgets.QPushButton()
            self.btn_inspect = QtWidgets.QPushButton()
            btn_row.addStretch(1)
            btn_row.addWidget(self.btn_add_files)
            btn_row.addWidget(self.btn_add_folder)
            btn_row.addWidget(self.btn_inspect)
            btn_row.addWidget(self.btn_clear)
            btn_row.addStretch(1)

            layout.addWidget(self.title_label)
            layout.addWidget(self.subtitle_label)
            layout.addLayout(btn_row)

        def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
            else:
                event.ignore()

        def dropEvent(self, event: QtGui.QDropEvent) -> None:
            paths: list[str] = []
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if local:
                    paths.append(local)
            if paths:
                self.files_dropped.emit(paths)
            event.acceptProposedAction()

    class PresetCardWidget(QtWidgets.QFrame):
        """Selectable card representing one of the 5 compression presets (Presets A-E)."""
        clicked = Signal(object)

        def __init__(
            self,
            preset_id: PresetId,
            parent: Optional[QtWidgets.QWidget] = None,
        ) -> None:
            super().__init__(parent)
            self.preset_id = preset_id
            self.setObjectName("PresetCard")
            self.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
            self.setMinimumWidth(185)
            self.setMinimumHeight(150)

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(6)

            top_row = QtWidgets.QHBoxLayout()
            self.badge_label = QtWidgets.QLabel()
            self.badge_label.setObjectName("BadgeLabel")
            top_row.addWidget(self.badge_label)
            top_row.addStretch(1)

            self.title_label = QtWidgets.QLabel()
            self.title_label.setStyleSheet("font-size: 15px; font-weight: 700;")
            self.subtitle_label = QtWidgets.QLabel()
            self.subtitle_label.setObjectName("MutedLabel")

            self.desc_label = QtWidgets.QLabel()
            self.desc_label.setWordWrap(True)
            self.desc_label.setObjectName("SubheadingLabel")

            layout.addLayout(top_row)
            layout.addWidget(self.title_label)
            layout.addWidget(self.subtitle_label)
            layout.addWidget(self.desc_label, 1)

        def set_selected(self, selected: bool) -> None:
            self.setProperty("selected", "true" if selected else "false")
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

        def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit(self.preset_id)
            super().mousePressEvent(event)

    class BeforeAfterCompareDialog(QtWidgets.QDialog):
        """Modal dialog showing representative before/after frames and short sample compression metrics."""

        def __init__(
            self,
            controller: AppController,
            parent: Optional[QtWidgets.QWidget] = None,
        ) -> None:
            super().__init__(parent)
            self.controller = controller
            self.setWindowTitle(self.controller.translator.t("btn_inspect_frames"))
            self.resize(960, 580)

            layout = QtWidgets.QVBoxLayout(self)
            self.summary_label = QtWidgets.QLabel()
            self.summary_label.setObjectName("SubheadingLabel")
            layout.addWidget(self.summary_label)

            frames_row = QtWidgets.QHBoxLayout()
            self.before_image = QtWidgets.QLabel("Original Frame")
            self.before_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.before_image.setMinimumSize(440, 360)
            self.after_image = QtWidgets.QLabel("Compressed Sample Frame")
            self.after_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.after_image.setMinimumSize(440, 360)
            frames_row.addWidget(self.before_image, 1)
            frames_row.addWidget(self.after_image, 1)
            layout.addLayout(frames_row, 1)

            btn_run = QtWidgets.QPushButton("Generate 2.5s Sample & Compare Detail")
            btn_run.setObjectName("PrimaryStartButton")
            btn_run.clicked.connect(self._run_sample)
            layout.addWidget(btn_run)

        def _run_sample(self) -> None:
            if not self.controller.selected_files:
                return
            media = self.controller.selected_files[0]
            tmp_dir = Path(tempfile.gettempdir()) / "pocketvideo_preview"
            try:
                sample_vid, before_png, after_png = self.controller.generate_before_after_preview(
                    media, tmp_dir, sample_duration=2.5
                )
                sample_size = sample_vid.stat().st_size
                self.summary_label.setText(
                    f"Source: {media.file_path.name} ({format_bytes(media.file_size_bytes)}) | "
                    f"Sample Output ({sample_vid.name}): {format_bytes(sample_size)}"
                )
                pix_before = QtGui.QPixmap(str(before_png)).scaled(
                    440, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                pix_after = QtGui.QPixmap(str(after_png)).scaled(
                    440, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                self.before_image.setPixmap(pix_before)
                self.after_image.setPixmap(pix_after)
            except Exception as exc:
                self.summary_label.setText(f"Preview error: {exc}")

    class CompletedVideoCompareDialog(QtWidgets.QDialog):
        """Compare aligned frames from the completed *real* source and output."""

        frames_ready = Signal(str, str)
        frames_error = Signal(str)

        def __init__(self, result: JobResultComparison, binaries, parent=None) -> None:
            super().__init__(parent)
            self.result = result
            self.binaries = binaries
            self._tmp = tempfile.TemporaryDirectory(prefix="pocketvideo_completed_compare_")
            self.setWindowTitle("PocketVideo — Before / After")
            self.resize(960, 580)
            self.frames_ready.connect(self._display_frames)
            self.frames_error.connect(self._display_error)

            layout = QtWidgets.QVBoxLayout(self)
            self.summary = QtWidgets.QLabel(
                f"{result.input_path.name} • {format_bytes(result.original_size_bytes)} → "
                f"{format_bytes(result.output_size_bytes)} • Saved {result.reduction_percent:.1f}%"
            )
            self.summary.setWordWrap(True)
            layout.addWidget(self.summary)
            self.status = QtWidgets.QLabel("Loading actual source/output frames…")
            layout.addWidget(self.status)
            frames = QtWidgets.QHBoxLayout()
            self.source_frame = QtWidgets.QLabel("Before")
            self.output_frame = QtWidgets.QLabel("After")
            for frame in (self.source_frame, self.output_frame):
                frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
                frame.setMinimumSize(380, 280)
                frame.setScaledContents(False)
                frames.addWidget(frame, 1)
            layout.addLayout(frames, 1)
            QtCore.QTimer.singleShot(0, self._extract_frames_async)

        def _extract_frames_async(self) -> None:
            def worker() -> None:
                try:
                    original = Path(self._tmp.name) / "original.png"
                    converted = Path(self._tmp.name) / "result.png"
                    extract_representative_frame(self.result.input_path, original, self.binaries)
                    extract_representative_frame(self.result.output_path, converted, self.binaries)
                    self.frames_ready.emit(str(original), str(converted))
                except Exception as exc:
                    self.frames_error.emit(str(exc))

            threading.Thread(target=worker, name="PocketVideoCompare", daemon=True).start()

        def _display_frames(self, original: str, converted: str) -> None:
            for path, label in ((original, self.source_frame), (converted, self.output_frame)):
                pixmap = QtGui.QPixmap(path)
                if pixmap.isNull():
                    self.status.setText("Cannot load the extracted frame.")
                    return
                label.setPixmap(pixmap.scaled(
                    440, 360, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            self.status.setText("Actual decoded video frames — same timestamp (0.5s)")

        def _display_error(self, message: str) -> None:
            self.status.setText(message)


    class PocketVideoMainWindow(QtWidgets.QMainWindow):
        """Main desktop window for PocketVideo."""

        def __init__(self, controller: AppController) -> None:
            super().__init__()
            self.controller = controller
            self.signals = UiBridgeSignals()
            self.signals.job_updated.connect(self._on_job_updated_ui)
            self.signals.queue_summary.connect(self._on_queue_summary_ui)
            self.signals.job_completed.connect(self._on_job_completed_ui)
            self.signals.preview_ready.connect(self._on_thumbnail_ready)
            self._preview_semaphore = threading.Semaphore(2)
            self._thumbnail_jobs: set[str] = set()
            self._last_import_paths: tuple[str, ...] = ()
            self._thumbnail_dir = Path(tempfile.gettempdir()) / "pocketvideo_previews"

            if self.controller.queue_manager:
                self.controller.queue_manager.on_job_updated = self.signals.job_updated.emit
                self.controller.queue_manager.on_queue_summary = self.signals.queue_summary.emit
                self.controller.queue_manager.on_job_completed = self.signals.job_completed.emit

            self.setMinimumSize(1120, 760)
            self.preset_cards: dict[PresetId, PresetCardWidget] = {}
            self.nav_buttons: list[QtWidgets.QPushButton] = []

            self._build_ui()
            self.apply_theme(self.controller.settings.theme)
            self.retranslate_ui()
            self._refresh_selection_summary()

        def _build_ui(self) -> None:
            root = QtWidgets.QWidget()
            root.setObjectName("RootContainer")
            self.setCentralWidget(root)

            main_h = QtWidgets.QHBoxLayout(root)
            main_h.setContentsMargins(0, 0, 0, 0)
            main_h.setSpacing(0)

            # Left navigation sidebar
            sidebar = QtWidgets.QFrame()
            sidebar.setObjectName("SidebarFrame")
            sidebar.setFixedWidth(235)
            side_v = QtWidgets.QVBoxLayout(sidebar)
            side_v.setContentsMargins(16, 20, 16, 20)
            side_v.setSpacing(8)

            self.brand_label = QtWidgets.QLabel("PocketVideo")
            self.brand_label.setObjectName("HeadingLabel")
            self.brand_sub = QtWidgets.QLabel("Android Video Studio")
            self.brand_sub.setObjectName("MutedLabel")
            side_v.addWidget(self.brand_label)
            side_v.addWidget(self.brand_sub)
            side_v.addSpacing(14)

            self.btn_nav_dashboard = QtWidgets.QPushButton()
            self.btn_nav_convert = QtWidgets.QPushButton()
            self.btn_nav_queue = QtWidgets.QPushButton()
            self.btn_nav_history = QtWidgets.QPushButton()
            self.btn_nav_settings = QtWidgets.QPushButton()

            for idx, btn in enumerate([
                self.btn_nav_dashboard,
                self.btn_nav_convert,
                self.btn_nav_queue,
                self.btn_nav_history,
                self.btn_nav_settings,
            ]):
                btn.setObjectName("NavButton")
                btn.clicked.connect(lambda _checked=False, i=idx: self._switch_page(i))
                side_v.addWidget(btn)
                self.nav_buttons.append(btn)

            side_v.addStretch(1)

            # Language & Theme quick selectors
            self.lang_combo = QtWidgets.QComboBox()
            self.lang_combo.addItem("ไทย (Thai)", "th")
            self.lang_combo.addItem("English", "en")
            self.lang_combo.setCurrentIndex(
                0 if self.controller.translator.language == "th" else 1
            )
            self.lang_combo.currentIndexChanged.connect(self._on_language_changed)

            self.theme_btn = QtWidgets.QPushButton()
            self.theme_btn.clicked.connect(self._toggle_theme)

            side_v.addWidget(self.lang_combo)
            side_v.addWidget(self.theme_btn)

            # Right stacked content area
            self.pages = QtWidgets.QStackedWidget()
            self.pages.addWidget(self._build_dashboard_page())
            self.pages.addWidget(self._build_convert_page())
            self.pages.addWidget(self._build_queue_page())
            self.pages.addWidget(self._build_history_page())
            self.pages.addWidget(self._build_settings_page())

            main_h.addWidget(sidebar)
            main_h.addWidget(self.pages, 1)
            self._switch_page(0)

        def _build_dashboard_page(self) -> QtWidgets.QWidget:
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(28, 24, 28, 24)
            v.setSpacing(16)

            self.header_title = QtWidgets.QLabel()
            self.header_title.setObjectName("HeadingLabel")
            self.header_subtitle = QtWidgets.QLabel()
            self.header_subtitle.setObjectName("SubheadingLabel")
            v.addWidget(self.header_title)
            v.addWidget(self.header_subtitle)

            # Drag and drop zone
            self.drop_zone = DragDropVideoZone()
            self.drop_zone.files_dropped.connect(self._on_paths_dropped)
            self.drop_zone.btn_add_files.clicked.connect(self._prompt_add_files)
            self.drop_zone.btn_add_folder.clicked.connect(self._prompt_add_folder)
            self.drop_zone.btn_clear.clicked.connect(self._clear_files)
            self.drop_zone.btn_inspect.clicked.connect(self._open_compare_dialog)
            v.addWidget(self.drop_zone)

            # File inspection summary & warnings banner
            self.file_summary_label = QtWidgets.QLabel()
            self.file_summary_label.setObjectName("SubheadingLabel")
            self.file_summary_label.setWordWrap(True)
            v.addWidget(self.file_summary_label)

            # The imported-file list is separate from the job queue. Removing an entry here
            # never deletes its original file or cancels an already queued job.
            self.file_table_title = QtWidgets.QLabel()
            self.file_table_title.setObjectName("HeadingLabel")
            v.addWidget(self.file_table_title)
            self.file_table = QtWidgets.QTableWidget(0, 4)
            self.file_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            self.file_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
            self.file_table.verticalHeader().setVisible(False)
            self.file_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
            self.file_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            self.file_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            self.file_table.setColumnWidth(0, 100)
            self.file_table.setMinimumHeight(140)
            self.file_table.setMaximumHeight(310)
            self.file_table.setIconSize(QtCore.QSize(88, 50))
            self.file_table.cellDoubleClicked.connect(self._preview_input_row)
            v.addWidget(self.file_table)
            self.file_table_title.setVisible(False)
            self.file_table.setVisible(False)

            self.warning_banner = QtWidgets.QLabel()
            self.warning_banner.setObjectName("WarningBanner")
            self.warning_banner.setWordWrap(True)
            self.warning_banner.setVisible(False)
            v.addWidget(self.warning_banner)

            # 5 Compression Preset Cards
            self.preset_section_label = QtWidgets.QLabel()
            self.preset_section_label.setObjectName("HeadingLabel")
            self.preset_disclaimer_label = QtWidgets.QLabel()
            self.preset_disclaimer_label.setObjectName("MutedLabel")
            v.addWidget(self.preset_section_label)
            v.addWidget(self.preset_disclaimer_label)

            cards_row = QtWidgets.QHBoxLayout()
            cards_row.setSpacing(12)
            for pid in PresetId:
                card = PresetCardWidget(pid)
                card.clicked.connect(self._on_preset_selected)
                self.preset_cards[pid] = card
                cards_row.addWidget(card, 1)

            self.preset_cards_widget = QtWidgets.QWidget()
            self.preset_cards_widget.setLayout(cards_row)

            # Interactive comparison table: a compact alternative to the existing cards.
            self.preset_comparison_title = QtWidgets.QLabel()
            self.preset_comparison_title.setObjectName("HeadingLabel")
            v.addWidget(self.preset_comparison_title)
            self.preset_table = QtWidgets.QTableWidget(len(PresetId), 4)
            self.preset_table.verticalHeader().setVisible(False)
            self.preset_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            self.preset_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
            self.preset_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
            self.preset_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
            self.preset_table.setMinimumHeight(180)
            self.preset_table.setMaximumHeight(225)
            self.preset_table.cellClicked.connect(
                lambda row, _col: self._on_preset_selected(list(PresetId)[row])
            )

            # Show only one presentation at a time: the short comparison table
            # is the default, with all five original preset cards a tab away.
            self.preset_tabs = QtWidgets.QTabWidget()
            self.preset_tabs.addTab(self.preset_table, "")
            self.preset_tabs.addTab(self.preset_cards_widget, "")
            v.addWidget(self.preset_tabs)
            prefs_row = QtWidgets.QHBoxLayout()
            self.pref_quality = QtWidgets.QCheckBox()
            self.pref_size = QtWidgets.QCheckBox()
            self.pref_speed = QtWidgets.QCheckBox()
            for p in (self.pref_quality, self.pref_size, self.pref_speed):
                prefs_row.addWidget(p)
                p.toggled.connect(self._on_preferences_changed)
            prefs_row.addStretch(1)
            v.addLayout(prefs_row)
            self.preference_message = QtWidgets.QLabel()
            self.preference_message.setObjectName("MutedLabel")
            self.preference_message.setWordWrap(True)
            v.addWidget(self.preference_message)
            self.encoding_details_label = QtWidgets.QLabel()
            self.encoding_details_label.setObjectName("SubheadingLabel")
            self.encoding_details_label.setWordWrap(True)
            v.addWidget(self.encoding_details_label)

            # Custom Target Size Bar (Preset E)
            self.target_size_frame = QtWidgets.QFrame()
            self.target_size_frame.setObjectName("CardFrame")
            ts_row = QtWidgets.QHBoxLayout(self.target_size_frame)
            self.target_size_label = QtWidgets.QLabel()
            self.target_size_spin = QtWidgets.QDoubleSpinBox()
            self.target_size_spin.setRange(1.0, 50_000.0)
            self.target_size_spin.setValue(self.controller.target_size_mb)
            self.target_size_spin.valueChanged.connect(self._on_target_size_changed)
            self.target_unit_combo = QtWidgets.QComboBox()
            self.target_unit_combo.addItems(["MB", "GB"])
            self.target_unit_combo.currentIndexChanged.connect(self._on_target_size_changed)
            self.target_plan_status = QtWidgets.QLabel()
            self.target_plan_status.setObjectName("SubheadingLabel")
            self.target_plan_status.setWordWrap(True)

            ts_row.addWidget(self.target_size_label)
            ts_row.addWidget(self.target_size_spin)
            ts_row.addWidget(self.target_unit_combo)
            ts_row.addWidget(self.target_plan_status, 1)
            v.addWidget(self.target_size_frame)

            # Output Format, Compatibility Mode, Destination Folder, and Start Button
            bottom_card = QtWidgets.QFrame()
            bottom_card.setObjectName("CardFrame")
            b_layout = QtWidgets.QGridLayout(bottom_card)
            b_layout.setContentsMargins(16, 14, 16, 14)
            b_layout.setSpacing(12)

            self.lbl_compat = QtWidgets.QLabel()
            self.compat_combo = QtWidgets.QComboBox()
            self.compat_combo.addItem("", CompatibilityMode.MODERN_ANDROID.value)
            self.compat_combo.addItem("", CompatibilityMode.MAX_COMPATIBILITY.value)
            self.compat_combo.currentIndexChanged.connect(self._on_compat_changed)

            self.lbl_format = QtWidgets.QLabel()
            self.format_combo = QtWidgets.QComboBox()
            for fmt in ["mp4", "mkv", "mov", "webm", "avi"]:
                self.format_combo.addItem(fmt.upper(), fmt)
            self.format_combo.currentIndexChanged.connect(self._on_format_changed)


            self.operation_label = QtWidgets.QLabel()
            self.operation_combo = QtWidgets.QComboBox()
            self.operation_combo.addItem("", "compress")
            self.operation_combo.addItem("", "convert")
            self.operation_combo.addItem("", "both")
            self.operation_combo.currentIndexChanged.connect(self._on_operation_changed)

            self.lbl_out_dir = QtWidgets.QLabel()
            self.out_dir_edit = QtWidgets.QLineEdit(str(self.controller.output_dir))
            self.btn_browse_out = QtWidgets.QPushButton()

            self.btn_browse_out.clicked.connect(self._browse_output_dir)
            self.btn_save_out_default = QtWidgets.QPushButton()
            self.btn_save_out_default.clicked.connect(self._save_default_folder)
            self.out_dir_edit.editingFinished.connect(self._sync_current_output_directory)

            self.btn_start_compress = QtWidgets.QPushButton()
            self.btn_start_compress.setObjectName("PrimaryStartButton")
            self.btn_start_compress.clicked.connect(self._start_compression_batch)


            b_layout.addWidget(self.operation_label, 0, 0)
            b_layout.addWidget(self.operation_combo, 0, 1, 1, 3)
            b_layout.addWidget(self.lbl_compat, 1, 0)

            b_layout.addWidget(self.compat_combo, 1, 1)
            b_layout.addWidget(self.lbl_format, 1, 2)
            b_layout.addWidget(self.format_combo, 1, 3)
            b_layout.addWidget(self.lbl_out_dir, 2, 0)
            b_layout.addWidget(self.out_dir_edit, 2, 1, 1, 2)
            b_layout.addWidget(self.btn_browse_out, 2, 3)
            b_layout.addWidget(self.btn_save_out_default, 3, 1, 1, 3)
            b_layout.addWidget(self.btn_start_compress, 4, 0, 1, 4)
            v.addWidget(bottom_card)

            # Expandable Advanced Settings Panel
            self.btn_toggle_advanced = QtWidgets.QPushButton()
            self.btn_toggle_advanced.setCheckable(True)
            self.btn_toggle_advanced.toggled.connect(self._toggle_advanced_panel)
            v.addWidget(self.btn_toggle_advanced)

            self.advanced_panel = self._build_advanced_panel()
            self.advanced_panel.setVisible(False)
            v.addWidget(self.advanced_panel)

            v.addStretch(1)
            scroll.setWidget(page)
            self._on_preset_selected(self.controller.active_preset)
            return scroll

        def _build_advanced_panel(self) -> QtWidgets.QFrame:
            frame = QtWidgets.QFrame()
            frame.setObjectName("CardFrame")
            grid = QtWidgets.QGridLayout(frame)
            grid.setContentsMargins(16, 14, 16, 14)
            grid.setSpacing(10)

            self.chk_adv_override = QtWidgets.QCheckBox("Enable Manual Encoder Overrides")
            self.chk_adv_override.toggled.connect(self._sync_advanced_options)

            self.adv_encoder_combo = QtWidgets.QComboBox()
            for enc in ["", "libx265", "libx264", "libvpx-vp9", "libsvtav1", "hevc_nvenc", "h264_nvenc", "hevc_qsv", "h264_qsv"]:
                self.adv_encoder_combo.addItem(enc or "Auto (Preset Default)", enc)
            self.adv_encoder_combo.currentIndexChanged.connect(self._sync_advanced_options)

            self.adv_crf_spin = QtWidgets.QSpinBox()
            self.adv_crf_spin.setRange(0, 51)
            self.adv_crf_spin.setValue(24)
            self.adv_crf_spin.valueChanged.connect(self._sync_advanced_options)

            self.adv_preset_combo = QtWidgets.QComboBox()
            for sp in ["ultrafast", "veryfast", "fast", "medium", "slow", "veryslow"]:
                self.adv_preset_combo.addItem(sp, sp)
            self.adv_preset_combo.setCurrentText("medium")
            self.adv_preset_combo.currentIndexChanged.connect(self._sync_advanced_options)

            self.adv_audio_combo = QtWidgets.QComboBox()
            for ac in ["copy", "aac", "libopus", "mp3"]:
                self.adv_audio_combo.addItem(ac, ac)
            self.adv_audio_combo.currentIndexChanged.connect(self._sync_advanced_options)

            grid.addWidget(self.chk_adv_override, 0, 0, 1, 4)
            grid.addWidget(QtWidgets.QLabel("Encoder:"), 1, 0)
            grid.addWidget(self.adv_encoder_combo, 1, 1)
            grid.addWidget(QtWidgets.QLabel("CRF (0-51):"), 1, 2)
            grid.addWidget(self.adv_crf_spin, 1, 3)
            grid.addWidget(QtWidgets.QLabel("Speed Preset:"), 2, 0)
            grid.addWidget(self.adv_preset_combo, 2, 1)
            grid.addWidget(QtWidgets.QLabel("Audio Codec:"), 2, 2)
            grid.addWidget(self.adv_audio_combo, 2, 3)
            return frame

        def _build_convert_page(self) -> QtWidgets.QWidget:
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(28, 24, 28, 24)
            v.setSpacing(14)

            self.convert_title = QtWidgets.QLabel()
            self.convert_title.setObjectName("HeadingLabel")
            v.addWidget(self.convert_title)

            card = QtWidgets.QFrame()
            card.setObjectName("CardFrame")
            cv = QtWidgets.QVBoxLayout(card)
            self.chk_smart_remux = QtWidgets.QCheckBox(
                "Use Smart Lossless Remux automatically when codecs match target container"
            )
            self.chk_smart_remux.setChecked(True)
            self.convert_mode_banner = QtWidgets.QLabel()
            self.convert_mode_banner.setObjectName("SubheadingLabel")
            self.convert_mode_banner.setWordWrap(True)

            self.btn_start_convert = QtWidgets.QPushButton()
            self.btn_start_convert.setObjectName("PrimaryStartButton")

            self.btn_start_convert.clicked.connect(self._go_to_convert_on_main)


            self.convert_help = QtWidgets.QLabel()
            self.convert_help.setWordWrap(True)
            cv.addWidget(self.convert_help)
            cv.addWidget(self.chk_smart_remux)
            cv.addWidget(self.convert_mode_banner)
            cv.addWidget(self.btn_start_convert)
            v.addWidget(card)
            v.addStretch(1)
            return page

        def _build_queue_page(self) -> QtWidgets.QWidget:
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(28, 24, 28, 24)
            v.setSpacing(12)

            self.queue_title = QtWidgets.QLabel()
            self.queue_title.setObjectName("HeadingLabel")
            v.addWidget(self.queue_title)

            self.overall_progress_bar = QtWidgets.QProgressBar()
            self.overall_progress_bar.setRange(0, 100)
            v.addWidget(self.overall_progress_bar)

            ctrl_row = QtWidgets.QHBoxLayout()
            self.btn_pause_job = QtWidgets.QPushButton()
            self.btn_resume_job = QtWidgets.QPushButton()
            self.btn_cancel_job = QtWidgets.QPushButton()
            self.btn_cancel_all = QtWidgets.QPushButton()
            self.btn_retry_failed = QtWidgets.QPushButton()

            self.btn_pause_job.clicked.connect(self._pause_selected_job)
            self.btn_resume_job.clicked.connect(self._resume_selected_job)
            self.btn_cancel_job.clicked.connect(self._cancel_selected_job)
            self.btn_cancel_all.clicked.connect(self._cancel_all_jobs)
            self.btn_retry_failed.clicked.connect(self._retry_failed_jobs)

            for b in (
                self.btn_pause_job,
                self.btn_resume_job,
                self.btn_cancel_job,
                self.btn_cancel_all,
                self.btn_retry_failed,
            ):
                ctrl_row.addWidget(b)
            ctrl_row.addStretch(1)
            v.addLayout(ctrl_row)


            self.queue_table = QtWidgets.QTableWidget(0, 8)
            self.queue_table.setHorizontalHeaderLabels([

                "Job ID", "File", "Status", "Progress", "FPS / Speed", "Elapsed / ETA", "Summary / Result", "Actions"
            ])
            self.queue_table.horizontalHeader().setStretchLastSection(True)
            self.queue_table.setSelectionBehavior(
                QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
            )
            v.addWidget(self.queue_table, 1)
            return page

        def _build_history_page(self) -> QtWidgets.QWidget:
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(28, 24, 28, 24)
            v.setSpacing(12)

            self.history_title = QtWidgets.QLabel()
            self.history_title.setObjectName("HeadingLabel")
            v.addWidget(self.history_title)

            self.history_table = QtWidgets.QTableWidget(0, 7)
            self.history_table.setHorizontalHeaderLabels([
                "File", "Original Size", "Output Size", "Space Saved", "Reduction", "Resolution", "Codec"
            ])
            self.history_table.horizontalHeader().setStretchLastSection(True)
            v.addWidget(self.history_table, 1)
            self._refresh_history_table()
            return page

        def _build_settings_page(self) -> QtWidgets.QWidget:
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(28, 24, 28, 24)
            v.setSpacing(14)

            self.settings_title = QtWidgets.QLabel()
            self.settings_title.setObjectName("HeadingLabel")
            v.addWidget(self.settings_title)

            card = QtWidgets.QFrame()
            card.setObjectName("CardFrame")
            grid = QtWidgets.QGridLayout(card)
            grid.setContentsMargins(18, 16, 18, 16)
            grid.setSpacing(12)

            self.res_mode_combo = QtWidgets.QComboBox()
            self.res_mode_combo.addItem("Light", ResourceMode.LIGHT.value)
            self.res_mode_combo.addItem("Balanced", ResourceMode.BALANCED.value)
            self.res_mode_combo.addItem("Maximum", ResourceMode.MAXIMUM.value)
            self.res_mode_combo.addItem("Custom", ResourceMode.CUSTOM.value)
            self.res_mode_combo.currentIndexChanged.connect(self._on_resource_mode_changed)


            self.cpu_cap_combo = QtWidgets.QComboBox()
            for cap in (50, 60, 70, 75, 80, 85, 90, 95, 100):
                self.cpu_cap_combo.addItem(f"{cap}%", cap)
            self._set_cpu_cap_selection(self.controller.resource_limits.cpu_hard_cap_percent)
            self.cpu_cap_combo.currentIndexChanged.connect(self._on_resource_controls_edited)

            self.threads_spin = QtWidgets.QSpinBox()
            self.threads_spin.setRange(0, 64)
            self.threads_spin.setValue(self.controller.resource_limits.cpu_threads)
            self.threads_spin.valueChanged.connect(self._on_resource_controls_edited)

            self.concurrent_spin = QtWidgets.QSpinBox()
            self.concurrent_spin.setRange(1, 4)
            self.concurrent_spin.setValue(self.controller.resource_limits.max_concurrent_jobs)
            self.concurrent_spin.valueChanged.connect(self._on_resource_controls_edited)

            self.chk_gpu_enable = QtWidgets.QCheckBox("Enable GPU Hardware Encoding when supported")
            self.chk_gpu_enable.setChecked(self.controller.resource_limits.enable_gpu_encoding)
            self.chk_gpu_enable.toggled.connect(self._on_resource_controls_edited)
            with QtCore.QSignalBlocker(self.res_mode_combo):
                idx = self.res_mode_combo.findData(self.controller.resource_limits.mode.value)
                self.res_mode_combo.setCurrentIndex(max(0, idx))

            self.lbl_hard_cap_note = QtWidgets.QLabel()
            self.lbl_hard_cap_note.setObjectName("SubheadingLabel")
            self.lbl_hard_cap_note.setWordWrap(True)

            self.lbl_best_effort_note = QtWidgets.QLabel()
            self.lbl_best_effort_note.setObjectName("MutedLabel")
            self.lbl_best_effort_note.setWordWrap(True)

            grid.addWidget(QtWidgets.QLabel("Resource Mode:"), 0, 0)
            grid.addWidget(self.res_mode_combo, 0, 1)
            grid.addWidget(QtWidgets.QLabel("CPU Hard Cap (%):"), 1, 0)

            grid.addWidget(self.cpu_cap_combo, 1, 1)
            grid.addWidget(QtWidgets.QLabel("CPU Thread Count (0=Auto):"), 2, 0)
            grid.addWidget(self.threads_spin, 2, 1)
            grid.addWidget(QtWidgets.QLabel("Concurrent Jobs:"), 3, 0)
            grid.addWidget(self.concurrent_spin, 3, 1)
            grid.addWidget(self.chk_gpu_enable, 4, 0, 1, 2)
            grid.addWidget(self.lbl_hard_cap_note, 5, 0, 1, 2)
            grid.addWidget(self.lbl_best_effort_note, 6, 0, 1, 2)

            v.addWidget(card)
            v.addStretch(1)
            return page

        def apply_theme(self, theme_name: str) -> None:
            self.controller.set_theme(theme_name)
            self.setStyleSheet(build_stylesheet(theme_name))

        def retranslate_ui(self) -> None:
            t = self.controller.translator.t
            self.setWindowTitle(t("app_title"))
            self.header_title.setText(t("app_title"))
            self.header_subtitle.setText(t("app_subtitle"))

            self.btn_nav_dashboard.setText(t("nav_dashboard"))
            self.btn_nav_convert.setText(t("nav_convert"))
            self.btn_nav_queue.setText(t("nav_queue"))
            self.btn_nav_history.setText(t("nav_history"))
            self.btn_nav_settings.setText(t("nav_settings"))

            self.drop_zone.title_label.setText(t("drop_title"))
            self.drop_zone.subtitle_label.setText(t("drop_subtitle"))
            self.drop_zone.btn_add_files.setText(t("btn_add_files"))
            self.drop_zone.btn_add_folder.setText(t("btn_add_folder"))
            self.drop_zone.btn_clear.setText(t("btn_clear_selection"))
            self.drop_zone.btn_inspect.setText(t("btn_inspect_frames"))
            self.file_table_title.setText(t("import_preview_header"))
            self.file_table.setHorizontalHeaderLabels([
                t("import_preview_col"), t("import_filename_col"),
                t("import_info_col"), t("import_action_col"),
            ])
            # Refresh buttons after language changes even when the file list is unchanged.
            self._last_import_paths = ()
            self._refresh_imported_file_table()

            self.preset_section_label.setText(t("preset_section_title"))

            self.preset_disclaimer_label.setText(t("preset_disclaimer"))

            self.preset_comparison_title.setText(t("preset_comparison_title"))
            self.preset_tabs.setTabText(0, "ตารางเปรียบเทียบ" if self.controller.translator.language == "th" else "Comparison Table")
            self.preset_tabs.setTabText(1, "Preset แบบเดิม" if self.controller.translator.language == "th" else "Preset Cards")
            self.preset_table.setHorizontalHeaderLabels([
                t("preset_col_name"), t("preset_col_size"),
                t("preset_col_speed"), t("preset_col_quality"),
            ])
            matrix = [
                ("preset_a_title", ("น้อย–ปานกลาง", "ช้า", "ดีมาก"), ("Low–medium", "Slow", "Excellent")),
                ("preset_b_title", ("ปานกลาง–มาก", "ช้า", "ดี"), ("Medium–high", "Slow", "Good")),
                ("preset_c_title", ("มาก", "ช้ามาก", "ลดลงบ้าง"), ("High", "Very slow", "Some quality loss")),
                ("preset_d_title", ("ปานกลาง", "เร็ว", "ปานกลาง–ดี"), ("Medium", "Fast", "Fair–good")),
                ("preset_e_title", ("ตามที่กำหนด", "ช้า (2-Pass)", "ขึ้นกับขนาดเป้าหมาย"), ("Target-based", "Slow (2-pass)", "Depends on target")),
            ]
            for row, (key, thai, english) in enumerate(matrix):
                values = (t(key), *(thai if self.controller.translator.language == "th" else english))
                for col, value in enumerate(values):
                    self.preset_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
            self.pref_quality.setText(t("pref_quality"))
            self.pref_size.setText(t("pref_size"))
            self.pref_speed.setText(t("pref_speed"))
            self.preference_message.setText(t("pref_info"))
            self._refresh_encoding_details()

            for pid, card in self.preset_cards.items():
                desc = PRESET_CATALOG[pid]
                card.badge_label.setText(t(desc.badge_key))
                card.title_label.setText(t(desc.title_key))
                card.subtitle_label.setText(t(desc.subtitle_key))
                card.desc_label.setText(t(desc.desc_key))

            self.target_size_label.setText(t("label_target_size"))
            self.lbl_compat.setText(t("label_compatibility"))
            self.compat_combo.setItemText(0, t("compat_modern_android"))
            self.compat_combo.setItemText(1, t("compat_max_compatibility"))
            self.lbl_format.setText(t("label_output_format"))

            self.lbl_out_dir.setText(t("label_output_folder"))
            self.btn_save_out_default.setText(t("btn_default_folder"))
            self.operation_label.setText(t("operation_label"))
            self.operation_combo.setItemText(0, t("operation_compress"))
            self.operation_combo.setItemText(1, t("operation_convert"))
            self.operation_combo.setItemText(2, t("operation_both"))
            self.btn_browse_out.setText(t("btn_browse_output"))

            self.btn_start_compress.setText(t("btn_start_compress"))
            self._on_operation_changed()
            self.btn_toggle_advanced.setText(t("advanced_toggle"))

            self.convert_title.setText(t("nav_convert"))

            self.btn_start_convert.setText(t("convert_go_dashboard"))
            self.convert_help.setText(t("convert_explanation"))

            self.queue_title.setText(t("nav_queue"))
            self.queue_table.setHorizontalHeaderItem(7, QtWidgets.QTableWidgetItem(t("queue_actions")))
            self.btn_pause_job.setText(t("queue_pause"))
            self.btn_resume_job.setText(t("queue_resume"))
            self.btn_cancel_job.setText(t("queue_cancel"))
            self.btn_cancel_all.setText(t("queue_cancel_all"))
            self.btn_retry_failed.setText(t("queue_retry_failed"))

            self.history_title.setText(t("comparison_header"))
            self.settings_title.setText(t("resource_section_title"))
            self.lbl_hard_cap_note.setText(t("resource_hard_cap_note"))
            self.lbl_best_effort_note.setText(t("resource_best_effort_note"))
            self.theme_btn.setText(
                t("theme_light") if self.controller.settings.theme == "dark" else t("theme_dark")
            )

        def _switch_page(self, index: int) -> None:
            self.pages.setCurrentIndex(index)
            for idx, btn in enumerate(self.nav_buttons):
                btn.setProperty("active", "true" if idx == index else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)

        def _on_language_changed(self, _index: int) -> None:
            lang = self.lang_combo.currentData()
            if lang:
                self.controller.set_language(str(lang))
                self.retranslate_ui()
                self._refresh_selection_summary()

        def _toggle_theme(self) -> None:
            next_theme = "light" if self.controller.settings.theme == "dark" else "dark"
            self.apply_theme(next_theme)
            self.retranslate_ui()


        def _on_preset_selected(self, preset_id: PresetId, *, from_preferences: bool = False) -> None:
            self.controller.active_preset = preset_id
            if not from_preferences:
                for preference in (self.pref_quality, self.pref_size, self.pref_speed):
                    with QtCore.QSignalBlocker(preference):
                        preference.setChecked(False)
                self.preference_message.setText(self.controller.translator.t("pref_info"))
            with QtCore.QSignalBlocker(self.preset_table):
                self.preset_table.selectRow(list(PresetId).index(preset_id))
            self._refresh_encoding_details()
            for pid, card in self.preset_cards.items():
                card.set_selected(pid == preset_id)
            self.target_size_frame.setVisible(preset_id == PresetId.CUSTOM_SIZE)
            self._refresh_selection_summary()


        def _on_preferences_changed(self, *_args: object) -> None:
            quality = self.pref_quality.isChecked()
            small = self.pref_size.isChecked()
            speed = self.pref_speed.isChecked()
            if not (quality or small or speed):
                self.preference_message.setText(self.controller.translator.t("pref_info"))
                return
            if small and quality:
                recommended = PresetId.BEST_BALANCE
            elif speed:
                recommended = PresetId.FASTEST
            elif small:
                recommended = PresetId.MAX_SPACE_SAVING
            else:
                recommended = PresetId.BEST_QUALITY
            self._on_preset_selected(recommended, from_preferences=True)
            self.preference_message.setText(
                self.controller.translator.t("pref_conflict")
                if sum((quality, small, speed)) > 1
                else self.controller.translator.t("preset_comparison_hint")
            )

        def _refresh_encoding_details(self) -> None:
            if not hasattr(self, "encoding_details_label"):
                return
            p = PRESET_CATALOG[self.controller.active_preset]
            if p.uses_two_pass:
                detail = f"2-Pass • Target {self.controller.target_size_mb:g} MB • size not exact"
            else:
                crf = (p.default_crf_h265 if self.controller.compatibility_mode ==
                       CompatibilityMode.MODERN_ANDROID else p.default_crf_h264)
                detail = f"1-Pass • CRF {crf} • Encoder preset: {p.encoder_preset}"
            codec = ("HEVC/H.265" if self.controller.compatibility_mode ==
                     CompatibilityMode.MODERN_ANDROID else "H.264")
            if p.prefers_gpu:
                detail += " • GPU (if supported)"

            if hasattr(self, "chk_adv_override") and self.chk_adv_override.isChecked():
                detail += " • Manual overrides enabled (see Advanced)"
            self.encoding_details_label.setText(f"{codec} • {detail} • {self.controller.target_container.upper()}")

        def _on_operation_changed(self, *_args: object) -> None:
            mode = self.operation_combo.currentData()
            t = self.controller.translator.t
            self.btn_start_compress.setText(
                t("btn_start_convert") if mode == "convert" else t("btn_start_compress")
            )

        def _on_target_size_changed(self) -> None:
            val = float(self.target_size_spin.value())
            if self.target_unit_combo.currentText() == "GB":
                val *= 1024.0

            self.controller.target_size_mb = val
            self._refresh_encoding_details()
            self._refresh_selection_summary()

        def _on_compat_changed(self) -> None:
            data = self.compat_combo.currentData()
            if data:

                self.controller.compatibility_mode = CompatibilityMode(str(data))
                self._refresh_encoding_details()
                self._refresh_selection_summary()

        def _on_format_changed(self) -> None:
            data = self.format_combo.currentData()
            if data:

                self.controller.target_container = str(data)
                self._refresh_encoding_details()
                self._refresh_selection_summary()

        def _browse_output_dir(self) -> None:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select Output Folder", str(self.controller.output_dir)
            )
            if chosen:

                self.controller.set_output_directory(chosen)
                self.out_dir_edit.setText(chosen)


        def _sync_current_output_directory(self) -> bool:
            path = self.out_dir_edit.text().strip()
            if not path:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", "Please select an output folder.")
                return False
            try:
                self.controller.set_output_directory(path)
            except (ValueError, OSError) as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))
                return False
            return True

        def _save_default_folder(self) -> None:
            try:
                folder = self.controller.set_output_directory(
                    self.out_dir_edit.text().strip(), remember=True
                )
            except (ValueError, OSError) as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))
                return
            self.out_dir_edit.setText(str(folder))
            self.btn_save_out_default.setText(self.controller.translator.t("default_folder_saved"))

        def _toggle_advanced_panel(self, checked: bool) -> None:
            self.advanced_panel.setVisible(checked)

        def _sync_advanced_options(self) -> None:
            adv = self.controller.advanced_options
            adv.override_enabled = self.chk_adv_override.isChecked()
            enc = self.adv_encoder_combo.currentData()
            adv.encoder_name = str(enc) if enc else None
            adv.crf = int(self.adv_crf_spin.value())
            adv.encoder_speed_preset = str(self.adv_preset_combo.currentData() or "medium")
            adv.audio_codec = str(self.adv_audio_combo.currentData() or "copy")

        def _on_paths_dropped(self, paths: list[str]) -> None:
            for p_str in paths:
                p = Path(p_str)
                if p.is_dir():
                    self.controller.add_folder(p)
                else:
                    self.controller.add_files([p])
            self._refresh_selection_summary()


        def _preview_input_row(self, row: int, _column: int) -> None:
            cell = self.file_table.item(row, 1)
            if cell:
                self._open_input_file(Path(str(cell.data(Qt.ItemDataRole.UserRole))))

        def _open_input_file(self, path: Path) -> None:
            """Ask Windows to open the existing original; never execute it as a command."""
            if path.is_file():
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

        def _remove_imported_file(self, path: Path) -> None:
            self.controller.remove_selected_file(path)
            self._refresh_selection_summary()

        def _refresh_imported_file_table(self) -> None:
            files = self.controller.selected_files
            keys = tuple(str(meta.file_path.resolve()) for meta in files)
            self.file_table_title.setVisible(bool(files))
            self.file_table.setVisible(bool(files))
            if keys == self._last_import_paths:
                return
            self._last_import_paths = keys
            self.file_table.setRowCount(len(files))
            t = self.controller.translator.t
            for row, media in enumerate(files):
                path = media.file_path.resolve()
                thumb = QtWidgets.QTableWidgetItem("🎞")
                self.file_table.setItem(row, 0, thumb)
                item = QtWidgets.QTableWidgetItem(path.name)
                item.setToolTip(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self.file_table.setItem(row, 1, item)
                v = media.primary_video
                details = f"{format_bytes(media.file_size_bytes)}"
                if v:
                    details += f" • {v.resolution_label} • {v.codec_name} • {v.fps:.1f}fps"
                self.file_table.setItem(row, 2, QtWidgets.QTableWidgetItem(details))
                actions = QtWidgets.QWidget()
                buttons = QtWidgets.QHBoxLayout(actions)
                buttons.setContentsMargins(2, 1, 2, 1)
                open_btn = QtWidgets.QPushButton(t("import_open"))
                remove_btn = QtWidgets.QPushButton(t("import_remove"))
                open_btn.clicked.connect(lambda _checked=False, p=path: self._open_input_file(p))
                remove_btn.clicked.connect(lambda _checked=False, p=path: self._remove_imported_file(p))
                buttons.addWidget(open_btn)
                buttons.addWidget(remove_btn)
                self.file_table.setCellWidget(row, 3, actions)
                self.file_table.setRowHeight(row, 58)
                if self.controller.binaries:
                    self._queue_thumbnail(media)

        def _queue_thumbnail(self, media) -> None:
            path = media.file_path.resolve()
            try:
                key = hashlib.sha256(f"{path}|{path.stat().st_mtime_ns}|{path.stat().st_size}".encode()).hexdigest()
            except OSError:
                return
            target = self._thumbnail_dir / (key + ".png")
            if target.exists():
                self._on_thumbnail_ready(str(path), str(target))
                return
            if key in self._thumbnail_jobs:
                return
            self._thumbnail_jobs.add(key)

            def worker() -> None:
                try:
                    with self._preview_semaphore:
                        extract_representative_frame(
                            path, target, self.controller.binaries,
                            duration_seconds=media.duration_seconds,
                        )
                    self.signals.preview_ready.emit(str(path), str(target))
                except Exception:
                    pass  # Thumbnail failures must never prevent processing the input.
                finally:
                    self._thumbnail_jobs.discard(key)

            threading.Thread(target=worker, name="PocketVideoThumbnail", daemon=True).start()

        def _on_thumbnail_ready(self, original: str, image_path: str) -> None:
            pixmap = QtGui.QPixmap(image_path)
            if pixmap.isNull():
                return
            pixmap = pixmap.scaled(
                88, 50, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            for row in range(self.file_table.rowCount()):
                item = self.file_table.item(row, 1)
                icon_cell = self.file_table.item(row, 0)
                if item and icon_cell and item.data(Qt.ItemDataRole.UserRole) == original:
                    icon_cell.setText("")
                    icon_cell.setIcon(QtGui.QIcon(pixmap))
                    break

        def _prompt_add_files(self) -> None:
            exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_VIDEO_EXTENSIONS))
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self, "Select Video Files", "", f"Video Files ({exts})"
            )
            if files:
                self.controller.add_files(files)
                self._refresh_selection_summary()

        def _prompt_add_folder(self) -> None:
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Video Folder")
            if folder:
                self.controller.add_folder(folder)
                self._refresh_selection_summary()

        def _clear_files(self) -> None:
            self.controller.clear_selected_files()
            self._refresh_selection_summary()

        def _open_compare_dialog(self) -> None:
            if not self.controller.selected_files:
                return
            dlg = BeforeAfterCompareDialog(self.controller, self)
            dlg.exec()

        def _refresh_selection_summary(self) -> None:
            files = self.controller.selected_files
            self._refresh_imported_file_table()
            if not files:
                self.file_summary_label.setText("No videos selected yet.")
                self.warning_banner.setVisible(False)
                self.target_plan_status.setText("")
                return

            total_bytes = sum(m.file_size_bytes for m in files)
            first = files[0]
            v = first.primary_video
            v_desc = (
                f"{v.resolution_label} • {v.fps:.0f}fps • {v.codec_name.upper()} • {v.hdr_format_name}"
                if v else "Unknown Video"
            )
            self.file_summary_label.setText(
                f"Selected: {len(files)} file(s) ({format_bytes(total_bytes)}) — Primary: {first.file_path.name} [{v_desc}]"
            )

            if self.controller.active_preset == PresetId.CUSTOM_SIZE:
                plan = self.controller.preview_target_size_plan(first)
                self.target_plan_status.setText(plan.explanation)

            mode, reason = self.controller.check_conversion_mode(
                first, self.controller.target_container
            )
            self.convert_mode_banner.setText(f"{mode.value}: {reason}")

            warnings = self.controller.evaluate_active_warnings()
            if warnings:
                self.warning_banner.setText("\n".join(warnings))
                self.warning_banner.setVisible(True)
            else:
                self.warning_banner.setVisible(False)

        def _start_compression_batch(self) -> None:
            if not self.controller.selected_files:
                return


            if not self._sync_current_output_directory():
                return
            selected_op = self.operation_combo.currentData()
            self.controller.enqueue_current_batch(
                operation_mode=(OperationMode.CONVERT_TRANSCODE if selected_op == "convert"
                                else OperationMode.COMPRESS),
                allow_smart_remux=(selected_op == "convert"),
            )
            self._switch_page(2)


        def _go_to_convert_on_main(self) -> None:
            self.operation_combo.setCurrentIndex(self.operation_combo.findData("convert"))
            self._switch_page(0)

        def _start_conversion_batch(self) -> None:
            if not self.controller.selected_files:
                return

            if not self._sync_current_output_directory():
                return
            self.controller.enqueue_current_batch(
                operation_mode=OperationMode.CONVERT_TRANSCODE,
                allow_smart_remux=self.chk_smart_remux.isChecked(),
            )
            self._switch_page(2)

        def _on_job_updated_ui(self, item: QueueItem) -> None:
            row = self._find_or_create_job_row(item.spec.job_id)
            self.queue_table.setItem(row, 0, QtWidgets.QTableWidgetItem(item.spec.job_id))
            self.queue_table.setItem(row, 1, QtWidgets.QTableWidgetItem(item.spec.input_path.name))
            self.queue_table.setItem(row, 2, QtWidgets.QTableWidgetItem(item.status.value.upper()))
            self.queue_table.setItem(row, 3, QtWidgets.QTableWidgetItem(f"{item.progress.percent:.1f}%"))
            self.queue_table.setItem(
                row, 4, QtWidgets.QTableWidgetItem(f"{item.progress.fps:.1f} fps ({item.progress.speed_multiplier:.2f}x)")
            )
            self.queue_table.setItem(
                row,
                5,
                QtWidgets.QTableWidgetItem(
                    f"{format_duration(item.progress.elapsed_seconds)} / {format_duration(item.progress.eta_seconds)}"
                ),
            )
            if item.result:
                res_txt = (
                    f"{format_bytes(item.result.original_size_bytes)} -> {format_bytes(item.result.output_size_bytes)} "
                    f"(-{item.result.reduction_percent:.1f}%)"
                )
            elif item.error_message:
                res_txt = item.error_message
            else:
                res_txt = item.resource_summary

            self.queue_table.setItem(row, 6, QtWidgets.QTableWidgetItem(res_txt))
            if item.result and item.result.output_path.is_file():
                if self.queue_table.cellWidget(row, 7) is None:
                    result = item.result
                    actions = QtWidgets.QWidget()
                    actions_row = QtWidgets.QHBoxLayout(actions)
                    actions_row.setContentsMargins(1, 0, 1, 0)
                    for title_key, callback in (
                        ("queue_open", lambda r=result: self._open_output_file(r)),
                        ("queue_folder", lambda r=result: self._open_output_folder(r)),
                        ("queue_compare", lambda r=result: self._show_completed_compare(r)),
                    ):
                        btn = QtWidgets.QPushButton(self.controller.translator.t(title_key))
                        btn.clicked.connect(lambda _checked=False, f=callback: f())
                        actions_row.addWidget(btn)
                    self.queue_table.setCellWidget(row, 7, actions)
                    self.queue_table.setColumnWidth(7, 330)
            elif self.queue_table.cellWidget(row, 7) is not None:
                self.queue_table.removeCellWidget(row, 7)

        def _open_output_file(self, result: JobResultComparison) -> None:
            if result.output_path.is_file():
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(result.output_path)))
            else:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", "Output file is missing.")

        def _open_output_folder(self, result: JobResultComparison) -> None:
            if result.output_path.parent.is_dir():
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(result.output_path.parent)))
            else:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", "Output folder is missing.")

        def _show_completed_compare(self, result: JobResultComparison) -> None:
            if not result.input_path.is_file() or not result.output_path.is_file():
                QtWidgets.QMessageBox.warning(self, "PocketVideo", "Input or output file is missing.")
                return
            if not self.controller.binaries:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", "FFmpeg is unavailable.")
                return
            dlg = CompletedVideoCompareDialog(result, self.controller.binaries, self)
            dlg.exec()

        def _find_or_create_job_row(self, job_id: str) -> int:
            for r in range(self.queue_table.rowCount()):
                cell = self.queue_table.item(r, 0)
                if cell and cell.text() == job_id:
                    return r
            new_row = self.queue_table.rowCount()
            self.queue_table.insertRow(new_row)
            return new_row

        def _on_queue_summary_ui(self, avg_pct: float, _completed: int, _total: int) -> None:
            self.overall_progress_bar.setValue(int(round(avg_pct)))

        def _on_job_completed_ui(self, _comparison: JobResultComparison) -> None:
            self._refresh_history_table()

        def _refresh_history_table(self) -> None:
            history = self.controller.settings_mgr.load_history()
            self.history_table.setRowCount(len(history))
            for idx, entry in enumerate(history):
                self.history_table.setItem(idx, 0, QtWidgets.QTableWidgetItem(entry.output_path.name))
                self.history_table.setItem(idx, 1, QtWidgets.QTableWidgetItem(format_bytes(entry.original_size_bytes)))
                self.history_table.setItem(idx, 2, QtWidgets.QTableWidgetItem(format_bytes(entry.output_size_bytes)))
                self.history_table.setItem(idx, 3, QtWidgets.QTableWidgetItem(format_bytes(entry.space_saved_bytes)))
                self.history_table.setItem(idx, 4, QtWidgets.QTableWidgetItem(f"{entry.reduction_percent:.1f}%"))
                self.history_table.setItem(
                    idx, 5, QtWidgets.QTableWidgetItem(f"{entry.original_resolution} -> {entry.output_resolution}")
                )
                self.history_table.setItem(
                    idx, 6, QtWidgets.QTableWidgetItem(f"{entry.original_codec} -> {entry.output_codec}")
                )

        def _selected_job_id(self) -> Optional[str]:
            row = self.queue_table.currentRow()
            if row < 0:
                return None
            cell = self.queue_table.item(row, 0)
            return cell.text() if cell else None

        def _pause_selected_job(self) -> None:
            jid = self._selected_job_id()
            if jid and self.controller.queue_manager:
                self.controller.queue_manager.pause_job(jid)

        def _resume_selected_job(self) -> None:
            jid = self._selected_job_id()
            if jid and self.controller.queue_manager:
                self.controller.queue_manager.resume_job(jid)

        def _cancel_selected_job(self) -> None:
            jid = self._selected_job_id()
            if jid and self.controller.queue_manager:
                self.controller.queue_manager.cancel_job(jid)

        def _cancel_all_jobs(self) -> None:
            if self.controller.queue_manager:
                self.controller.queue_manager.cancel_all()

        def _retry_failed_jobs(self) -> None:
            if self.controller.queue_manager:
                self.controller.queue_manager.retry_failed()


        def _set_cpu_cap_selection(self, value: int) -> None:
            value = int(value)
            idx = self.cpu_cap_combo.findData(value)
            if idx < 0:
                # Preserve a saved value (including the built-in 40% Light mode)
                # rather than silently increasing the user's CPU budget.
                label = self.controller.translator.t("cpu_saved_custom", value=value)
                self.cpu_cap_combo.addItem(label, value)
                idx = self.cpu_cap_combo.findData(value)
            with QtCore.QSignalBlocker(self.cpu_cap_combo):
                self.cpu_cap_combo.setCurrentIndex(idx)

        def _on_resource_mode_changed(self) -> None:
            mode_val = self.res_mode_combo.currentData()
            if mode_val:
                mode = ResourceMode(str(mode_val))
                if mode == ResourceMode.CUSTOM:
                    return
                limits = self.controller.apply_resource_mode(mode)

                for widget in (self.cpu_cap_combo, self.threads_spin, self.concurrent_spin, self.chk_gpu_enable):
                    widget.blockSignals(True)
                try:

                    self._set_cpu_cap_selection(limits.cpu_hard_cap_percent)
                    self.threads_spin.setValue(limits.cpu_threads)
                    self.concurrent_spin.setValue(limits.max_concurrent_jobs)
                    self.chk_gpu_enable.setChecked(limits.enable_gpu_encoding)
                finally:

                    for widget in (self.cpu_cap_combo, self.threads_spin, self.concurrent_spin, self.chk_gpu_enable):
                        widget.blockSignals(False)

        def _on_resource_controls_edited(self, *_args: object) -> None:
            self.controller.update_resource_controls(

                cpu_hard_cap_percent=int(self.cpu_cap_combo.currentData()),
                cpu_threads=self.threads_spin.value(),
                max_concurrent_jobs=self.concurrent_spin.value(),
                enable_gpu_encoding=self.chk_gpu_enable.isChecked(),
            )
            with QtCore.QSignalBlocker(self.res_mode_combo):
                idx = self.res_mode_combo.findData(ResourceMode.CUSTOM.value)
                self.res_mode_combo.setCurrentIndex(idx)
