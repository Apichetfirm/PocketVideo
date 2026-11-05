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
import sys
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
from pocketvideo.core.savings import share_summary
from pocketvideo.core.original_cleanup import verified_original_for_trash
from pocketvideo.core.models import (
    CompatibilityMode,
    JobResultComparison,
    JobStatus,
    OperationMode,
    PresetId,
    ProcessPriority,
    ResourceMode,
    OutputPolicy,
    AfterExportAction,
    ImagePreset,
)
from pocketvideo.media.presets import PRESET_CATALOG
from pocketvideo.media.encoder import extract_representative_frame
from pocketvideo.media.images import (
    SUPPORTED_IMAGE_EXTENSIONS, IMAGE_OUTPUT_FORMATS, thumbnail_png, inspect_image,
)
from pocketvideo.media.probe import inspect_media
from pocketvideo.resources.post_export import run_after_export
from pocketvideo.queue.manager import (
    QueueItem, SUPPORTED_VIDEO_EXTENSIONS, discover_videos_in_folder,
)
from pocketvideo.ui.controller import AppController
from pocketvideo.ui.theme import build_stylesheet, DARK_PALETTE, LIGHT_PALETTE
from pocketvideo.ui.file_actions import reveal_file_in_location
from pocketvideo.ui.icons import app_icon, decorate_main_window, assign, icon


if PYSIDE6_AVAILABLE:

    class UiBridgeSignals(QtCore.QObject):
        """Thread-safe Qt signal bridge from background QueueManager threads to the GUI thread."""
        job_updated = Signal(object)
        queue_summary = Signal(float, int, int)
        job_completed = Signal(object)
        history_error = Signal(str)
        preview_ready = Signal(str, str)
        import_ready = Signal(object, object)

    class WheelSafeComboBox(QtWidgets.QComboBox):
        """Mouse wheel scrolls the enclosing page, not the selected setting."""
        def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
            event.ignore()

    class WheelSafeSpinBox(QtWidgets.QSpinBox):
        def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
            event.ignore()

    class WheelSafeDoubleSpinBox(QtWidgets.QDoubleSpinBox):
        def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
            event.ignore()

    class ConcurrentJobsComboBox(WheelSafeComboBox):
        """Dropdown with the spinbox-compatible methods used by resource tests."""
        def value(self) -> int:
            return int(self.currentData() or 1)

        def setValue(self, value: int) -> None:
            index = self.findData(max(1, min(8, int(value))))
            self.setCurrentIndex(index if index >= 0 else 0)

    class DragDropVideoZone(QtWidgets.QFrame):
        """Large drag-and-drop area accepting multiple video files or folders."""
        files_dropped = Signal(list)

        def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
            super().__init__(parent)
            self.setObjectName("DropZoneFrame")
            self.setAcceptDrops(True)
            self.setMinimumHeight(116)

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(16, 10, 16, 10)
            layout.setSpacing(8)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            self.title_label = QtWidgets.QLabel()
            self.title_label.setObjectName("HeadingLabel")
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            self.subtitle_label = QtWidgets.QLabel()
            self.subtitle_label.setObjectName("SubheadingLabel")
            self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)


            # Use a 2x2 button grid so long Thai labels do not force the entire
            # dashboard to scroll horizontally on 1366px laptops.
            btn_row = QtWidgets.QGridLayout()
            btn_row.setHorizontalSpacing(10)
            btn_row.setVerticalSpacing(6)
            self.btn_add_files = QtWidgets.QPushButton()
            self.btn_add_folder = QtWidgets.QPushButton()
            self.btn_clear = QtWidgets.QPushButton()
            self.btn_inspect = QtWidgets.QPushButton()

            btn_row.addWidget(self.btn_add_files, 0, 0)
            btn_row.addWidget(self.btn_add_folder, 0, 1)
            btn_row.addWidget(self.btn_inspect, 1, 0)
            btn_row.addWidget(self.btn_clear, 1, 1)
            btn_row.setColumnStretch(0, 1)
            btn_row.setColumnStretch(1, 1)

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
        """Asynchronously generate a short sample, then compare two real videos."""

        sample_ready = Signal(str, int)
        sample_failed = Signal(str)

        def __init__(self, controller: AppController, parent=None) -> None:
            super().__init__(parent)
            self.controller = controller
            self._scratch = tempfile.TemporaryDirectory(prefix="pocketvideo_sample_")
            self._busy = False
            self.setWindowTitle("PocketVideo — ทดลองบีบอัดและเทียบวิดีโอ")
            self.resize(520, 185)
            layout = QtWidgets.QVBoxLayout(self)
            self.summary_label = QtWidgets.QLabel(
                "สร้างคลิปทดลอง 2.5 วินาที เพื่อเล่นต้นฉบับและไฟล์บีบอัดพร้อมกัน"
            )
            self.summary_label.setWordWrap(True)
            layout.addWidget(self.summary_label)
            self.status_label = QtWidgets.QLabel("ไฟล์ต้นฉบับจะไม่ถูกเปลี่ยนแปลง")
            self.status_label.setObjectName("MutedLabel")
            layout.addWidget(self.status_label)
            self.run_button = QtWidgets.QPushButton("▶ สร้างตัวอย่างและเปิดตัวเล่นคู่")
            self.run_button.setObjectName("PrimaryStartButton")
            self.run_button.clicked.connect(self._run_sample)
            layout.addWidget(self.run_button)
            self.sample_ready.connect(self._show_player)
            self.sample_failed.connect(self._on_error)

        def _run_sample(self) -> None:
            media = next(
                (m for m in self.controller.selected_files if m.media_kind == "video"), None
            )
            if media is None or self._busy:
                return
            self._busy = True
            self.run_button.setEnabled(False)
            self.status_label.setText("กำลังสร้างคลิปทดลองด้วย FFmpeg…")
            source = media.file_path
            offset_ms = int(
                min(max(0.0, media.duration_seconds * 0.2),
                    max(0.0, media.duration_seconds - 2.5)) * 1000
            )
            self._sample_source = source

            def background() -> None:
                try:
                    video, _before_png, _after_png = self.controller.generate_before_after_preview(
                        media, Path(self._scratch.name), sample_duration=2.5
                    )
                    self.sample_ready.emit(str(video), offset_ms)
                except Exception as exc:
                    self.sample_failed.emit(str(exc))

            threading.Thread(target=background, name="PocketVideoSample", daemon=True).start()

        def _show_player(self, sample_path: str, offset_ms: int) -> None:
            self._busy = False
            self.run_button.setEnabled(True)
            self.status_label.setText("สร้างตัวอย่างเสร็จแล้ว")
            from pocketvideo.ui.video_compare import SynchronizedVideoCompareDialog
            try:
                dialog = SynchronizedVideoCompareDialog(
                    self._sample_source, Path(sample_path), self.controller.binaries,
                    self, before_offset_ms=offset_ms,
                )
                dialog.exec()
            except (OSError, RuntimeError) as exc:
                self.status_label.setText(f"ตัวเล่นวิดีโอไม่พร้อม: {exc}")

        def _on_error(self, error: str) -> None:
            self._busy = False
            self.run_button.setEnabled(True)
            self.status_label.setText("สร้างตัวอย่างไม่สำเร็จ: " + error)

        def closeEvent(self, event: QtGui.QCloseEvent) -> None:
            if self._busy:
                event.ignore()
                self.status_label.setText("กำลังสร้างตัวอย่าง ปิดได้เมื่อประมวลผลเสร็จ")
                return
            self._scratch.cleanup()
            super().closeEvent(event)

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
                    source_path = self.result.source_backup_path or self.result.input_path
                    if source_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                        thumbnail_png(source_path, original, (840, 700))
                        thumbnail_png(self.result.output_path, converted, (840, 700))
                    else:
                        extract_representative_frame(source_path, original, self.binaries)
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
            self.status.setText(
                "Actual decoded images" if self.result.input_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                else "Actual decoded video frames — same timestamp (0.5s)"
            )

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
            self.signals.history_error.connect(self._on_history_error_ui)
            self.signals.preview_ready.connect(self._on_thumbnail_ready)
            self.signals.import_ready.connect(self._on_import_ready)
            self._import_busy = False
            self._pending_imports: list[list[str]] = []
            self._preview_semaphore = threading.Semaphore(2)
            self._thumbnail_jobs: set[str] = set()
            self._last_import_paths: tuple[str, ...] = ()
            self._power_batch_ids: set[str] = set()
            self._power_batch_action = AfterExportAction.NONE
            self._power_prompt_pending = False
            self._power_verified_outputs: list[Path] = []
            self._usb_error_alerted: set[str] = set()
            self._thumbnail_dir = Path(tempfile.gettempdir()) / "pocketvideo_previews"

            if self.controller.queue_manager:
                self.controller.queue_manager.on_job_updated = self.signals.job_updated.emit
                self.controller.queue_manager.on_queue_summary = self.signals.queue_summary.emit
                self.controller.queue_manager.on_job_completed = self._persist_and_relay_completed

            self.setMinimumSize(1020, 690)
            self.preset_cards: dict[PresetId, PresetCardWidget] = {}
            self.nav_buttons: list[QtWidgets.QPushButton] = []

            self._theme_css_cache = {name: build_stylesheet(name) for name in ("dark", "light")}
            self._applied_theme = None
            self._build_ui()
            decorate_main_window(self)
            self.apply_theme(self.controller.settings.theme)
            self.retranslate_ui()
            self._refresh_selection_summary()
            if self.controller.queue_manager:
                for recovered in self.controller.queue_manager.items():
                    self._on_job_updated_ui(recovered)

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
            sidebar.setMinimumWidth(140)
            sidebar.setMaximumWidth(340)
            side_v = QtWidgets.QVBoxLayout(sidebar)
            side_v.setContentsMargins(16, 20, 16, 20)
            side_v.setSpacing(8)

            self.brand_label = QtWidgets.QLabel("PocketVideo")
            self.brand_label.setObjectName("HeadingLabel")
            self.brand_sub = QtWidgets.QLabel("Android Video Studio")
            self.brand_sub.setObjectName("MutedLabel")
            self.brand_icon_label = QtWidgets.QLabel()
            self.brand_icon_label.setObjectName("BrandIcon")
            self.brand_icon_label.setPixmap(app_icon(56).pixmap(44, 44))
            self.brand_icon_label.setFixedSize(44, 44)
            self.brand_icon_label.setAccessibleName("PocketVideo application icon")
            brand_row = QtWidgets.QHBoxLayout()
            brand_row.setSpacing(10)
            brand_row.addWidget(self.brand_icon_label)
            brand_copy = QtWidgets.QVBoxLayout()
            brand_copy.setContentsMargins(0, 0, 0, 0)
            brand_copy.setSpacing(1)
            brand_copy.addWidget(self.brand_label)
            brand_copy.addWidget(self.brand_sub)
            brand_row.addLayout(brand_copy, 1)
            side_v.addLayout(brand_row)
            # Visible creator credit; keep the original open-source attribution
            # in repository/licensing metadata separately.
            self.creator_credit_label = QtWidgets.QLabel("Created by Firm Impulse")
            self.creator_credit_label.setObjectName("MutedLabel")
            self.creator_credit_label.setAccessibleName("Creator: Firm Impulse")
            side_v.addWidget(self.creator_credit_label)
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

            self.btn_nav_convert.hide()  # Convert is now a dashboard choice.
            side_v.addStretch(1)

            # Language & Theme quick selectors
            self.lang_combo = WheelSafeComboBox()
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

            # User-resizable navigation; keep useful space for dense queue data.
            self.main_splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
            self.main_splitter.setChildrenCollapsible(False)
            self.main_splitter.addWidget(sidebar)
            self.main_splitter.addWidget(self.pages)
            self.main_splitter.setStretchFactor(0, 0)
            self.main_splitter.setStretchFactor(1, 1)
            self.main_splitter.setSizes([self.controller.settings.queue_sidebar_width, 980])
            self.main_splitter.splitterMoved.connect(self._save_sidebar_width)
            main_h.addWidget(self.main_splitter, 1)
            self._switch_page(0)

        def _build_dashboard_page(self) -> QtWidgets.QWidget:
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

            page = QtWidgets.QWidget()
            page.setObjectName("DashboardPage")
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(20, 16, 20, 16)
            v.setSpacing(10)

            self.header_title = QtWidgets.QLabel()
            self.header_title.setObjectName("HeadingLabel")
            self.header_subtitle = QtWidgets.QLabel()
            self.header_subtitle.setObjectName("SubheadingLabel")
            v.addWidget(self.header_title)
            # The sidebar and drop zone already explain the product; avoid
            # stacking a second marketing sentence above the work area.
            self.header_subtitle.hide()

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
            self.file_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
            self.file_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Fixed)
            self.file_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Fixed)
            self.file_table.setColumnWidth(0, 82)
            self.file_table.setColumnWidth(2, 215)
            self.file_table.setColumnWidth(3, 146)
            self.file_table.setMinimumHeight(140)
            self.file_table.setMaximumHeight(225)
            self.file_table.setIconSize(QtCore.QSize(74, 44))
            self.file_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
            self.preset_disclaimer_label.hide()

            cards_row = QtWidgets.QHBoxLayout()
            cards_row.setSpacing(12)
            for pid in PresetId:
                card = PresetCardWidget(pid)
                card.clicked.connect(self._on_preset_selected)
                self.preset_cards[pid] = card
                cards_row.addWidget(card, 1)


            self.preset_cards_widget = QtWidgets.QWidget()
            self.preset_cards_widget.setLayout(cards_row)
            self.preset_cards_scroll = QtWidgets.QScrollArea()
            self.preset_cards_scroll.setWidgetResizable(False)
            self.preset_cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.preset_cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.preset_cards_scroll.setMinimumHeight(210)
            self.preset_cards_scroll.setWidget(self.preset_cards_widget)

            # Interactive comparison table: a compact alternative to the existing cards.
            self.preset_comparison_title = QtWidgets.QLabel()
            self.preset_comparison_title.setObjectName("HeadingLabel")
            self.preset_comparison_title.hide()
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

            self.preset_tabs.addTab(self.preset_cards_scroll, "")
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
            self.preference_message.setVisible(False)
            self.encoding_details_label = QtWidgets.QLabel()
            self.encoding_details_label.setObjectName("SubheadingLabel")
            self.encoding_details_label.setWordWrap(True)
            v.addWidget(self.encoding_details_label)
            self.chk_max_two_pass = QtWidgets.QCheckBox()
            self.chk_max_two_pass.setChecked(self.controller.advanced_options.max_saving_two_pass)
            self.chk_max_two_pass.toggled.connect(self._sync_advanced_options)
            self.chk_max_two_pass.toggled.connect(self._refresh_encoding_details)
            v.addWidget(self.chk_max_two_pass)

            # Custom Target Size Bar (Preset E)
            self.target_size_frame = QtWidgets.QFrame()
            self.target_size_frame.setObjectName("CardFrame")
            ts_row = QtWidgets.QHBoxLayout(self.target_size_frame)
            self.target_size_label = QtWidgets.QLabel()
            self.target_size_spin = WheelSafeDoubleSpinBox()
            self.target_size_spin.setRange(1.0, 50_000.0)
            self.target_size_spin.setValue(self.controller.target_size_mb)
            self.target_size_spin.valueChanged.connect(self._on_target_size_changed)
            self.target_unit_combo = WheelSafeComboBox()
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
            self.compat_combo = WheelSafeComboBox()
            self.compat_combo.addItem("", CompatibilityMode.MODERN_ANDROID.value)
            self.compat_combo.addItem("", CompatibilityMode.MAX_COMPATIBILITY.value)
            self.compat_combo.currentIndexChanged.connect(self._on_compat_changed)

            self.lbl_format = QtWidgets.QLabel()
            self.format_combo = WheelSafeComboBox()
            for fmt in ["mp4", "mkv", "mov", "webm", "avi"]:
                self.format_combo.addItem(fmt.upper(), fmt)
            self.format_combo.currentIndexChanged.connect(self._on_format_changed)


            self.operation_label = QtWidgets.QLabel()
            self.operation_combo = WheelSafeComboBox()
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


            # Independent still-image format/quality controls work in mixed
            # batches without changing the selected VIDEO preset.
            self.lbl_image_format = QtWidgets.QLabel()
            self.image_format_combo = WheelSafeComboBox()
            for ext in IMAGE_OUTPUT_FORMATS:
                self.image_format_combo.addItem(ext.upper(), ext)
            self.image_format_combo.setCurrentIndex(
                max(0, self.image_format_combo.findData(self.controller.image_output_format))
            )
            self.image_format_combo.currentIndexChanged.connect(self._on_output_preferences_changed)

            self.lbl_image_preset = QtWidgets.QLabel()
            self.image_preset_combo = WheelSafeComboBox()
            for profile in ImagePreset:
                self.image_preset_combo.addItem("", profile.value)
            self.image_preset_combo.setCurrentIndex(
                max(0, self.image_preset_combo.findData(self.controller.image_preset.value))
            )
            self.image_preset_combo.currentIndexChanged.connect(self._on_output_preferences_changed)
            self.lbl_image_quality = QtWidgets.QLabel()
            self.image_quality_spin = WheelSafeSpinBox()
            self.image_quality_spin.setRange(1, 100)
            self.image_quality_spin.setValue(self.controller.image_quality)
            self.image_quality_spin.valueChanged.connect(self._on_output_preferences_changed)

            self.lbl_suffix = QtWidgets.QLabel()
            self.suffix_edit = QtWidgets.QLineEdit(self.controller.filename_suffix)
            self.suffix_edit.setMaxLength(48)
            self.suffix_edit.editingFinished.connect(self._on_output_preferences_changed)
            self.lbl_collision = QtWidgets.QLabel()
            self.output_policy_combo = WheelSafeComboBox()
            for policy in OutputPolicy:
                self.output_policy_combo.addItem("", policy.value)
            self.output_policy_combo.setCurrentIndex(
                max(0, self.output_policy_combo.findData(self.controller.output_policy.value))
            )
            self.output_policy_combo.currentIndexChanged.connect(self._on_output_preferences_changed)
            self.skip_larger_check = QtWidgets.QCheckBox()
            self.skip_larger_check.setChecked(self.controller.settings.skip_larger_outputs)
            self.skip_larger_check.toggled.connect(self._on_output_preferences_changed)
            self.lbl_after = QtWidgets.QLabel()
            self.after_action_combo = WheelSafeComboBox()
            for action in AfterExportAction:
                self.after_action_combo.addItem("", action.value)
            self.after_action_combo.setCurrentIndex(
                max(0, self.after_action_combo.findData(
                    self.controller.settings.after_export_action))
            )
            if sys.platform != "win32":
                # No native macOS power-action integration yet; avoid exposing
                # non-working choices or silently using Windows commands.
                self.after_action_combo.setCurrentIndex(0)
                for idx in (1, 2):
                    self.after_action_combo.model().item(idx).setEnabled(False)
            self.after_action_combo.currentIndexChanged.connect(self._on_output_preferences_changed)

            self.btn_start_compress = QtWidgets.QPushButton()
            self.btn_start_compress.setObjectName("PrimaryStartButton")
            self.btn_start_compress.clicked.connect(self._start_compression_batch)


            # Keep the always-needed controls and start button in a small card.
            # Image profiles, suffix, overwrite and power actions are opt-in
            # progressive disclosure, not a form users must scroll through.
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

            self.btn_toggle_export_options = QtWidgets.QPushButton()
            self.btn_toggle_export_options.setCheckable(True)
            self.export_options = QtWidgets.QFrame()
            self.export_options.setObjectName("CardFrame")
            options_layout = QtWidgets.QGridLayout(self.export_options)
            options_layout.setContentsMargins(16, 12, 16, 12)
            options_layout.setHorizontalSpacing(12)
            options_layout.setVerticalSpacing(10)
            options_layout.addWidget(self.lbl_image_format, 0, 0)
            options_layout.addWidget(self.image_format_combo, 0, 1)
            options_layout.addWidget(self.lbl_image_preset, 0, 2)
            options_layout.addWidget(self.image_preset_combo, 0, 3)
            options_layout.addWidget(self.lbl_image_quality, 1, 0)
            options_layout.addWidget(self.image_quality_spin, 1, 1)
            options_layout.addWidget(self.lbl_suffix, 2, 0)
            options_layout.addWidget(self.suffix_edit, 2, 1)
            options_layout.addWidget(self.lbl_collision, 2, 2)
            options_layout.addWidget(self.output_policy_combo, 2, 3)
            options_layout.addWidget(self.skip_larger_check, 3, 0, 1, 4)
            options_layout.addWidget(self.lbl_after, 4, 0)
            options_layout.addWidget(self.after_action_combo, 4, 1, 1, 3)
            self.export_options.setVisible(False)
            self.btn_toggle_export_options.toggled.connect(self.export_options.setVisible)
            self.btn_toggle_export_options.toggled.connect(
                lambda _checked: self._refresh_export_toggle_caption()
            )
            v.addWidget(bottom_card)
            v.addWidget(self.btn_toggle_export_options)
            v.addWidget(self.export_options)

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

            self.adv_encoder_combo = WheelSafeComboBox()
            for enc in ["", "libx265", "libx264", "libvpx-vp9", "libsvtav1",
                        "hevc_nvenc", "h264_nvenc", "hevc_qsv", "h264_qsv",
                        "hevc_amf", "h264_amf", "hevc_videotoolbox",
                        "h264_videotoolbox"]:
                self.adv_encoder_combo.addItem(enc or "Auto (Preset Default)", enc)
            self.adv_encoder_combo.currentIndexChanged.connect(self._sync_advanced_options)

            self.adv_crf_spin = WheelSafeSpinBox()
            self.adv_crf_spin.setRange(0, 51)
            self.adv_crf_spin.setValue(24)
            self.adv_crf_spin.valueChanged.connect(self._sync_advanced_options)

            self.adv_preset_combo = WheelSafeComboBox()
            for sp in ["ultrafast", "veryfast", "fast", "medium", "slow", "veryslow"]:
                self.adv_preset_combo.addItem(sp, sp)
            self.adv_preset_combo.setCurrentText("medium")
            self.adv_preset_combo.currentIndexChanged.connect(self._sync_advanced_options)

            self.adv_audio_combo = WheelSafeComboBox()
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

        def _save_sidebar_width(self, *_args: object) -> None:
            self.controller.settings.queue_sidebar_width = self.main_splitter.sizes()[0]
            self.controller.settings_mgr.save_settings()

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

            ctrl_row = QtWidgets.QGridLayout()
            ctrl_row.setHorizontalSpacing(8)
            ctrl_row.setVerticalSpacing(8)
            self.btn_pause_job = QtWidgets.QPushButton()
            self.btn_resume_job = QtWidgets.QPushButton()
            self.btn_cancel_job = QtWidgets.QPushButton()
            self.btn_cancel_all = QtWidgets.QPushButton()
            self.btn_retry_failed = QtWidgets.QPushButton()
            self.btn_clear_queue_history = QtWidgets.QPushButton()
            self.btn_delete_all_originals = QtWidgets.QPushButton()
            self.btn_clear_queue_history.clicked.connect(self._clear_queue_history)
            self.btn_delete_all_originals.clicked.connect(self._trash_all_originals)

            self.btn_pause_job.clicked.connect(self._pause_selected_job)
            self.btn_resume_job.clicked.connect(self._resume_selected_job)
            self.btn_cancel_job.clicked.connect(self._cancel_selected_job)
            self.btn_cancel_all.clicked.connect(self._cancel_all_jobs)
            self.btn_retry_failed.clicked.connect(self._retry_failed_jobs)

            for idx, b in enumerate((
                self.btn_pause_job,
                self.btn_resume_job,
                self.btn_cancel_job,
                self.btn_cancel_all,
                self.btn_retry_failed,
            )):
                ctrl_row.addWidget(b, idx // 3, idx % 3)
            self.queue_columns_btn = QtWidgets.QToolButton()
            self.queue_columns_btn.setPopupMode(
                QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
            )
            self.queue_columns_menu = QtWidgets.QMenu(self.queue_columns_btn)
            self.queue_columns_btn.setMenu(self.queue_columns_menu)
            ctrl_row.addWidget(self.queue_columns_btn, 1, 2)
            ctrl_row.addWidget(self.btn_clear_queue_history, 2, 0)
            ctrl_row.addWidget(self.btn_delete_all_originals, 2, 1, 1, 2)
            v.addLayout(ctrl_row)

            self.queue_table = QtWidgets.QTableWidget(0, 11)
            self.queue_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
            self.queue_table.setAlternatingRowColors(True)
            header = self.queue_table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionsMovable(True)
            header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
            self._queue_column_defaults = {
                0: 105, 1: 210, 2: 240, 3: 112, 4: 108, 5: 142,
                6: 155, 7: 260, 8: 205, 9: 162, 10: 162,
            }
            # Column visibility, sizes and order survive application restart.
            self._queue_column_actions = {}
            self._restoring_queue_layout = True
            for idx in range(11):
                action = self.queue_columns_menu.addAction("")
                action.setCheckable(True)
                action.setChecked(idx in self.controller.settings.queue_visible_columns)
                if idx == 1:
                    action.setChecked(True)
                    action.setEnabled(False)  # file name is always present
                action.toggled.connect(
                    lambda checked, col=idx: self._set_queue_column_visibility(col, checked)
                )
                self._queue_column_actions[idx] = action
                self.queue_table.setColumnWidth(
                    idx, self.controller.settings.queue_column_widths.get(
                        str(idx), self._queue_column_defaults[idx]
                    )
                )
                self.queue_table.setColumnHidden(idx, not action.isChecked())
            order = self.controller.settings.queue_column_order or [
                1, 9, 10, 3, 4, 5, 7, 2, 8, 6, 0,
            ]
            for visual, logical in enumerate(order):
                if 0 <= logical < 11:
                    header.moveSection(header.visualIndex(logical), visual)
            self._restoring_queue_layout = False
            header.sectionResized.connect(self._save_queue_column_width)
            header.sectionMoved.connect(self._save_queue_column_order)
            self.queue_table.setSelectionBehavior(
                QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
            )
            v.addWidget(self.queue_table, 1)
            return page

        def _set_queue_column_visibility(self, column: int, visible: bool) -> None:
            if column == 1:
                return
            self.queue_table.setColumnHidden(column, not visible)
            self._save_queue_column_visibility()

        def _save_queue_column_visibility(self) -> None:
            if getattr(self, "_restoring_queue_layout", False):
                return
            self.controller.settings.queue_visible_columns = [
                col for col in range(11) if not self.queue_table.isColumnHidden(col)
            ]
            self.controller.settings_mgr.save_settings()

        def _save_queue_column_width(self, column: int, _old: int, new: int) -> None:
            if getattr(self, "_restoring_queue_layout", False):
                return
            if new > 0:
                self.controller.settings.queue_column_widths[str(column)] = new
                self.controller.settings_mgr.save_settings()

        def _save_queue_column_order(self, *_args: object) -> None:
            if getattr(self, "_restoring_queue_layout", False):
                return
            head = self.queue_table.horizontalHeader()
            self.controller.settings.queue_column_order = [
                head.logicalIndex(v) for v in range(head.count())
            ]
            self.controller.settings_mgr.save_settings()

        def _build_history_page(self) -> QtWidgets.QWidget:
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            v.setContentsMargins(20, 16, 20, 16)
            v.setSpacing(12)

            head = QtWidgets.QHBoxLayout()
            self.history_title = QtWidgets.QLabel()
            self.history_title.setObjectName("HeadingLabel")
            head.addWidget(self.history_title, 1)
            self.btn_history_refresh = QtWidgets.QPushButton()
            assign(self.btn_history_refresh, "retry")
            self.btn_history_refresh.clicked.connect(self._refresh_history_table)
            head.addWidget(self.btn_history_refresh)
            self.btn_clear_visible_history = QtWidgets.QPushButton()
            self.btn_clear_visible_history.clicked.connect(self._clear_visible_history)
            head.addWidget(self.btn_clear_visible_history)
            v.addLayout(head)

            self.history_summary_card = QtWidgets.QFrame()
            self.history_summary_card.setObjectName("CardFrame")
            summary = QtWidgets.QVBoxLayout(self.history_summary_card)
            summary.setContentsMargins(20, 16, 20, 16)
            summary.setSpacing(8)
            self.history_summary_heading = QtWidgets.QLabel()
            self.history_summary_heading.setObjectName("SubheadingLabel")
            summary.addWidget(self.history_summary_heading)

            main_row = QtWidgets.QHBoxLayout()
            self.history_total_value = QtWidgets.QLabel("0 B")
            self.history_total_value.setObjectName("SavingsTotalValue")
            self.history_total_value.setMinimumHeight(46)
            self.history_total_value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            main_row.addWidget(self.history_total_value, 1)
            self.btn_copy_history = QtWidgets.QPushButton()
            assign(self.btn_copy_history, "save")
            self.btn_copy_history.clicked.connect(self._copy_history_stats)
            main_row.addWidget(self.btn_copy_history)
            self.btn_copy_history_image = QtWidgets.QPushButton()
            assign(self.btn_copy_history_image, "compare")
            self.btn_copy_history_image.clicked.connect(self._copy_history_image)
            main_row.addWidget(self.btn_copy_history_image)
            summary.addLayout(main_row)

            detail_row = QtWidgets.QHBoxLayout()
            detail_row.setSpacing(16)
            self.history_jobs_value = QtWidgets.QLabel()
            self.history_jobs_value.setObjectName("SubheadingLabel")
            self.history_ratio_value = QtWidgets.QLabel()
            self.history_ratio_value.setObjectName("SubheadingLabel")
            detail_row.addWidget(self.history_jobs_value)
            detail_row.addWidget(self.history_ratio_value)
            detail_row.addStretch(1)
            summary.addLayout(detail_row)
            self.history_quote = QtWidgets.QLabel()
            self.history_quote.setObjectName("SavingsQuote")
            self.history_quote.setWordWrap(True)
            summary.addWidget(self.history_quote)
            self.history_note = QtWidgets.QLabel()
            self.history_note.setObjectName("MutedLabel")
            self.history_note.setWordWrap(True)
            summary.addWidget(self.history_note)
            v.addWidget(self.history_summary_card)

            self.history_table = QtWidgets.QTableWidget(0, 9)
            self.history_table.setAlternatingRowColors(True)
            self.history_table.setSelectionBehavior(
                QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
            )
            self.history_table.setEditTriggers(
                QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
            )
            history_head = self.history_table.horizontalHeader()
            history_head.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
            history_head.setSectionResizeMode(8, QtWidgets.QHeaderView.ResizeMode.Fixed)
            self.history_table.setColumnWidth(8, 124)
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

            self.res_mode_combo = WheelSafeComboBox()
            self.res_mode_combo.addItem("Light", ResourceMode.LIGHT.value)
            self.res_mode_combo.addItem("Balanced", ResourceMode.BALANCED.value)
            self.res_mode_combo.addItem("Maximum", ResourceMode.MAXIMUM.value)
            self.res_mode_combo.addItem("Custom", ResourceMode.CUSTOM.value)
            self.res_mode_combo.currentIndexChanged.connect(self._on_resource_mode_changed)


            self.cpu_cap_combo = WheelSafeComboBox()
            self.cpu_cap_presets = (10, 20, 30, 40, 50, 60, 70, 75, 80, 85, 90, 95, 100)
            for cap in self.cpu_cap_presets:
                self.cpu_cap_combo.addItem(f"{cap}%", cap)
            self.cpu_cap_combo.addItem("กำหนดเอง… / Custom…", "custom")
            self.cpu_custom_spin = WheelSafeSpinBox()
            self.cpu_custom_spin.setRange(10, 100)
            self.cpu_custom_spin.setSuffix("%")
            self.cpu_custom_spin.setSingleStep(1)
            self.cpu_custom_spin.setToolTip("10–100% • Windows CPU Job Object hard cap")
            self.cpu_cap_row = QtWidgets.QHBoxLayout()
            self.cpu_cap_row.setContentsMargins(0, 0, 0, 0)
            self.cpu_cap_row.addWidget(self.cpu_cap_combo, 2)
            self.cpu_cap_row.addWidget(self.cpu_custom_spin, 1)
            self.cpu_custom_spin.setVisible(False)
            self._set_cpu_cap_selection(self.controller.resource_limits.cpu_hard_cap_percent)
            self.cpu_cap_combo.currentIndexChanged.connect(self._on_cpu_cap_choice_changed)
            self.cpu_custom_spin.valueChanged.connect(self._on_resource_controls_edited)

            self.threads_spin = WheelSafeSpinBox()
            self.threads_spin.setRange(0, 64)
            self.threads_spin.setValue(self.controller.resource_limits.cpu_threads)
            self.threads_spin.valueChanged.connect(self._on_resource_controls_edited)
            self.threads_spin.setToolTip(
                "0 = Auto. Requests FFmpeg encoding worker threads, not CPU %. "
                "Some codecs manage additional threads internally."
            )
            self.lbl_threads_help = QtWidgets.QLabel()
            self.lbl_threads_help.setObjectName("MutedLabel")
            self.lbl_threads_help.setWordWrap(True)

            self.concurrent_spin = ConcurrentJobsComboBox()
            for count in range(1, 9):
                self.concurrent_spin.addItem(str(count), count)
            self.concurrent_spin.setValue(self.controller.resource_limits.max_concurrent_jobs)
            self.concurrent_spin.currentIndexChanged.connect(self._on_resource_controls_edited)

            self.chk_gpu_enable = QtWidgets.QCheckBox("Enable GPU Hardware Encoding when supported")
            self.chk_gpu_enable.setChecked(self.controller.resource_limits.enable_gpu_encoding)
            self.chk_gpu_enable.toggled.connect(self._on_resource_controls_edited)
            self.gpu_backend_combo = WheelSafeComboBox()
            for label, backend in (
                ("Auto • detect working GPU", "auto"),
                ("NVIDIA NVENC", "nvenc"),
                ("Intel Quick Sync (QSV)", "qsv"),
                ("AMD AMF", "amf"),
                ("Apple VideoToolbox (macOS)", "videotoolbox"),
            ):
                self.gpu_backend_combo.addItem(label, backend)
            preferred = self.controller.resource_limits.preferred_gpu_encoder
            self.gpu_backend_combo.setCurrentIndex(
                max(0, self.gpu_backend_combo.findData(preferred))
            )
            self.gpu_backend_combo.currentIndexChanged.connect(
                self._on_resource_controls_edited
            )
            available = getattr(
                self.controller.binaries, "available_video_encoders", set()
            ) if self.controller.binaries else set()
            possible = sorted(name for name in available if any(
                backend in name for backend in ("nvenc", "qsv", "amf", "videotoolbox")
            ))
            self.gpu_backend_combo.setToolTip(
                "FFmpeg-listed GPU encoders: " + (", ".join(possible) or "none")
                + ". Selected hardware is verified by a test encode; CPU is fallback."
            )
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

            grid.addLayout(self.cpu_cap_row, 1, 1)
            self.lbl_thread_count = QtWidgets.QLabel("FFmpeg Threads (0=Auto):")
            grid.addWidget(self.lbl_thread_count, 2, 0)
            grid.addWidget(self.threads_spin, 2, 1)
            grid.addWidget(QtWidgets.QLabel("Concurrent Jobs:"), 3, 0)
            grid.addWidget(self.concurrent_spin, 3, 1)
            grid.addWidget(self.chk_gpu_enable, 4, 0, 1, 2)
            grid.addWidget(QtWidgets.QLabel("GPU Accelerator:"), 5, 0)
            grid.addWidget(self.gpu_backend_combo, 5, 1)
            grid.addWidget(self.lbl_hard_cap_note, 6, 0, 1, 2)
            grid.addWidget(self.lbl_best_effort_note, 7, 0, 1, 2)
            grid.addWidget(self.lbl_threads_help, 8, 0, 1, 2)
            if sys.platform == "darwin":
                self.cpu_cap_combo.setEnabled(False)
                self.cpu_custom_spin.setEnabled(False)
                self.lbl_hard_cap_note.setText(
                    "macOS uses process priority and FFmpeg threads; "
                    "Windows CPU Job Object hard caps are unavailable."
                )

            v.addWidget(card)
            v.addStretch(1)
            return page

        def apply_theme(self, theme_name: str) -> None:
            if getattr(self, "_applied_theme", None) == theme_name:
                return
            self.controller.set_theme(theme_name)
            self.setUpdatesEnabled(False)
            try:
                self.setStyleSheet(self._theme_css_cache[theme_name])
                self._applied_theme = theme_name
            finally:
                self.setUpdatesEnabled(True)

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
            self.file_table_title.setText(t("import_preview_header").split("(")[0].strip())
            self.file_table.setHorizontalHeaderLabels([
                t("import_preview_col"), t("import_filename_col"),
                t("import_info_col"), t("import_action_col"),
            ])
            # Refresh buttons after language changes even when the file list is unchanged.
            self._last_import_paths = ()
            self._refresh_imported_file_table()

            self.preset_section_label.setText(
                "โหมดบีบอัด • เลือกแถวที่เหมาะกับคุณ"
                if self.controller.translator.language == "th"
                else "Compression presets • choose a row"
            )

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

            self.lbl_image_format.setText(t("image_format_label"))
            self.lbl_image_preset.setText(t("image_profile_label"))
            self.lbl_image_quality.setText(t("image_quality_label"))
            for row, key in enumerate(
                ("image_quality_best", "image_quality_balanced", "image_quality_small",
                 "image_quality_fast", "image_quality_custom")
            ):
                self.image_preset_combo.setItemText(row, t(key))
            self.lbl_suffix.setText(t("filename_suffix_label"))
            self.lbl_collision.setText(t("output_policy_label"))
            for row, key in enumerate(
                ("output_policy_keep", "output_policy_skip", "output_policy_replace_target",
                 "output_policy_replace_source")
            ):
                self.output_policy_combo.setItemText(row, t(key))
            self.skip_larger_check.setText(
                "ไม่บันทึกผลบีบอัดที่ใหญ่กว่าต้นฉบับ"
                if self.controller.translator.language == "th"
                else "Discard compressed output if not smaller than original"
            )
            self.lbl_after.setText(t("after_export_label"))
            for row, key in enumerate(
                ("after_export_none", "after_export_sleep", "after_export_shutdown")
            ):
                self.after_action_combo.setItemText(row, t(key))
            self.image_quality_spin.setEnabled(
                self.image_preset_combo.currentData() == ImagePreset.CUSTOM.value
            )
            self.btn_toggle_export_options.setText(
                ("ตั้งค่า Export เพิ่มเติม ▴" if self.btn_toggle_export_options.isChecked()
                 else "ตั้งค่า Export เพิ่มเติม ▾")
                if self.controller.translator.language == "th"
                else ("More export settings ▴" if self.btn_toggle_export_options.isChecked()
                      else "More export settings ▾")
            )
            self.btn_start_compress.setText(t("btn_start_compress"))
            self._on_operation_changed()
            self.btn_toggle_advanced.setText(t("advanced_toggle"))
            self.chk_max_two_pass.setText(
                "เปิด 2-Pass (ช้าลง • ตั้งเป้าบิตเรตวิดีโอประมาณครึ่งหนึ่งของต้นฉบับ)"
                if self.controller.translator.language == "th"
                else "Enable 2-Pass (slower • targets ~50% of source video bitrate)"
            )
            self.chk_max_two_pass.setToolTip(
                "2-Pass ใช้การควบคุมบิตเรต ไม่ใช่ CRF • ขนาดจริงและคุณภาพขึ้นอยู่กับไฟล์"
                if self.controller.translator.language == "th"
                else "Switches from CRF to bitrate-based 2-pass. Size and quality vary by content."
            )

            self.convert_title.setText(t("nav_convert"))

            self.btn_start_convert.setText(t("convert_go_dashboard"))
            self.convert_help.setText(t("convert_explanation"))

            self.queue_title.setText(t("nav_queue"))
            self.queue_columns_btn.setText(t("queue_columns") + " ▾")
            queue_headers = (
                "queue_job_id", "queue_file", "queue_destination", "queue_status",
                "queue_progress", "queue_speed", "queue_duration", "queue_result",
                "queue_actions", "queue_start", "queue_finished",
            )
            for col, key in enumerate(queue_headers):
                self.queue_table.setHorizontalHeaderItem(
                    col, QtWidgets.QTableWidgetItem(t(key))
                )
                self._queue_column_actions[col].setText(t(key))
            self.btn_pause_job.setText(t("queue_pause"))
            self.btn_resume_job.setText(t("queue_resume"))
            self.btn_cancel_job.setText(t("queue_cancel"))
            self.btn_cancel_all.setText(t("queue_cancel_all"))
            self.btn_retry_failed.setText(t("queue_retry_failed"))
            self.btn_clear_queue_history.setText(
                "ล้างงานที่จบแล้ว" if self.controller.translator.language == "th"
                else "Clear Finished Jobs"
            )
            self.btn_delete_all_originals.setText(
                "ย้ายต้นฉบับทั้งหมดลงถังขยะ" if self.controller.translator.language == "th"
                else "Move All Originals to Trash"
            )

            self.history_title.setText(t("comparison_header"))
            self.history_summary_heading.setText(t("history_lifetime_title"))
            self.history_quote.setText(t("history_lifetime_quote"))
            self.history_note.setText(t("history_lifetime_note"))
            self.btn_copy_history.setText(t("history_copy_text"))
            self.btn_copy_history_image.setText(t("history_copy_image"))
            self.btn_history_refresh.setText(t("history_refresh"))
            self.btn_clear_visible_history.setText(
                "ล้างประวัติ" if self.controller.translator.language == "th"
                else "Clear History"
            )
            self.history_table.setHorizontalHeaderLabels([
                t("queue_file"), t("comparison_original"), t("comparison_compressed"),
                t("comparison_saved"), t("comparison_reduction"),
                "Resolution", "Codec", t("history_file_state"), t("history_actions"),
            ])
            self._refresh_history_table()
            self.settings_title.setText(t("resource_section_title"))
            self.lbl_hard_cap_note.setText(
                "macOS: CPU threads/priority are best-effort; Windows-only CPU hard cap unavailable."
                if sys.platform == "darwin" else t("resource_hard_cap_note")
            )
            self.lbl_best_effort_note.setText(t("resource_best_effort_note"))
            self.lbl_thread_count.setText(t("resource_threads_label"))
            self.lbl_threads_help.setText(t("resource_threads_explanation"))
            self.cpu_cap_combo.setItemText(
                self.cpu_cap_combo.findData("custom"), t("resource_cpu_custom")
            )
            self.theme_btn.setText(
                t("theme_light") if self.controller.settings.theme == "dark" else t("theme_dark")
            )

        def _switch_page(self, index: int) -> None:
            self.pages.setCurrentIndex(index)
            if index == 3:
                self._refresh_history_table()
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
            self.theme_btn.setText(self.controller.translator.t(
                "theme_light" if next_theme == "dark" else "theme_dark"
            ))
            # Do NOT retranslate/scan up to 2,000 historical file paths just to
            # switch colors (USB/network stat() can block the GUI thread).
            self._restyle_history_summary()


        def _on_preset_selected(self, preset_id: PresetId, *, from_preferences: bool = False) -> None:
            self.controller.active_preset = preset_id
            if not from_preferences:
                for preference in (self.pref_quality, self.pref_size, self.pref_speed):
                    with QtCore.QSignalBlocker(preference):
                        preference.setChecked(False)
                self.preference_message.setText(self.controller.translator.t("pref_info"))
                self.preference_message.setVisible(False)
            with QtCore.QSignalBlocker(self.preset_table):
                self.preset_table.selectRow(list(PresetId).index(preset_id))
            self._refresh_encoding_details()
            for pid, card in self.preset_cards.items():
                card.set_selected(pid == preset_id)
            self.target_size_frame.setVisible(preset_id == PresetId.CUSTOM_SIZE)
            self.chk_max_two_pass.setVisible(preset_id == PresetId.MAX_SPACE_SAVING)
            self._refresh_selection_summary()


        def _on_preferences_changed(self, *_args: object) -> None:
            quality = self.pref_quality.isChecked()
            small = self.pref_size.isChecked()
            speed = self.pref_speed.isChecked()
            if not (quality or small or speed):
                self.preference_message.setText(self.controller.translator.t("pref_info"))
                self.preference_message.setVisible(False)
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
            self.preference_message.setVisible(True)
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
            elif (p.preset_id == PresetId.MAX_SPACE_SAVING
                  and self.chk_max_two_pass.isChecked()):
                detail = "2-Pass • ~50% source video bitrate target • slower; size/quality vary"
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
            adv.max_saving_two_pass = self.chk_max_two_pass.isChecked()

        def _on_paths_dropped(self, paths: list[str]) -> None:
            """Never run ffprobe or image decoding on the Qt event thread."""
            if paths:
                self._pending_imports.append(list(paths))
                self._begin_next_import()

        def _begin_next_import(self) -> None:
            if self._import_busy or not self._pending_imports:
                return
            pending = self._pending_imports.pop(0)
            self._import_busy = True
            self.drop_zone.btn_add_files.setEnabled(False)
            self.drop_zone.btn_add_folder.setEnabled(False)
            self.drop_zone.btn_clear.setEnabled(False)
            self.btn_start_compress.setEnabled(False)
            old = {m.file_path.resolve() for m in self.controller.selected_files}
            bins = self.controller.binaries

            def inspect_in_background() -> None:
                found = []
                errors = []
                seen = set(old)
                for raw in pending:
                    p = Path(raw).expanduser()
                    if p.is_dir():
                        candidates = discover_videos_in_folder(p)
                    else:
                        candidates = [p]
                    for candidate in candidates:
                        try:
                            candidate = candidate.resolve()
                            if candidate in seen:
                                continue
                            seen.add(candidate)
                            if candidate.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                                meta = inspect_image(candidate)
                            elif bins is not None:
                                meta = inspect_media(candidate, bins)
                            else:
                                raise RuntimeError("FFmpeg/FFprobe missing")
                            found.append(meta)
                        except Exception as exc:
                            errors.append(f"{candidate.name}: {exc}")
                self.signals.import_ready.emit(found, errors)

            threading.Thread(
                target=inspect_in_background,
                name="PocketVideoMediaInspection",
                daemon=True,
            ).start()

        def _on_import_ready(self, media: object, errors: object) -> None:
            # All Qt widget mutations occur on the GUI thread.
            try:
                existing = {m.file_path.resolve() for m in self.controller.selected_files}
                for item in media:
                    if item.file_path.resolve() not in existing:
                        self.controller.selected_files.append(item)
                        existing.add(item.file_path.resolve())
                self._refresh_selection_summary()
                if errors:
                    QtWidgets.QMessageBox.warning(
                        self, "PocketVideo",
                        self.controller.translator.t("import_error") +
                        "\n" + "\n".join(errors[:12]),
                    )
            finally:
                self._import_busy = False
                self.drop_zone.btn_add_files.setEnabled(True)
                self.drop_zone.btn_add_folder.setEnabled(True)
                self.drop_zone.btn_clear.setEnabled(True)
                self.btn_start_compress.setEnabled(True)
                self._begin_next_import()

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
                thumb = QtWidgets.QTableWidgetItem("🖼" if media.media_kind == "image" else "🎞")
                self.file_table.setItem(row, 0, thumb)
                item = QtWidgets.QTableWidgetItem(path.name)
                item.setToolTip(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self.file_table.setItem(row, 1, item)
                v = media.primary_video
                details = f"{format_bytes(media.file_size_bytes)}"
                if v:
                    details += f" • {v.resolution_label} • {v.codec_name} • {v.fps:.1f}fps"
                elif media.media_kind == "image":
                    details += f" • Photo {media.image_width}x{media.image_height} • {media.container_format.upper()}"
                details_item = QtWidgets.QTableWidgetItem(details)
                details_item.setToolTip(details)
                self.file_table.setItem(row, 2, details_item)
                actions = QtWidgets.QWidget()
                buttons = QtWidgets.QHBoxLayout(actions)
                buttons.setContentsMargins(2, 1, 2, 1)
                open_btn = QtWidgets.QToolButton()
                open_btn.setText(t("import_open"))
                remove_btn = QtWidgets.QToolButton()
                remove_btn.setText("✕")
                remove_btn.setToolTip(t("import_remove"))
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
                        if media.media_kind == "image":
                            thumbnail_png(path, target)
                        else:
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
                74, 44, Qt.AspectRatioMode.KeepAspectRatio,
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
            exts = " ".join(f"*{ext}" for ext in sorted(
                SUPPORTED_VIDEO_EXTENSIONS | SUPPORTED_IMAGE_EXTENSIONS
            ))
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self, "Select Videos & Photos", "", f"Videos & Photos ({exts})"
            )
            if files:
                self._on_paths_dropped(files)

        def _prompt_add_folder(self) -> None:
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select Media Folder"
            )
            if folder:
                self._on_paths_dropped([folder])

        def _clear_files(self) -> None:
            self.controller.clear_selected_files()
            self._refresh_selection_summary()

        def _open_compare_dialog(self) -> None:
            video = next((m for m in self.controller.selected_files if m.media_kind == "video"), None)
            if video is None:
                QtWidgets.QMessageBox.information(
                    self, "PocketVideo", "Select a video for a short compression sample."
                )
                return
            original = list(self.controller.selected_files)
            try:
                self.controller.selected_files = [video, *(m for m in original if m is not video)]
                dlg = BeforeAfterCompareDialog(self.controller, self)
                dlg.exec()
            finally:
                self.controller.selected_files = original

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
                if v else (
                    f"Photo {first.image_width}x{first.image_height} • {first.container_format.upper()}"
                    if first.media_kind == "image" else "Unknown Video"
                )
            )
            self.file_summary_label.setText(
                f"{'เลือก' if self.controller.translator.language == 'th' else 'Selected'} "
                f"{len(files)} files • {format_bytes(total_bytes)} • {first.file_path.name} [{v_desc}]"
            )

            video = next((m for m in files if m.media_kind == "video"), None)
            if self.controller.active_preset == PresetId.CUSTOM_SIZE and video:
                plan = self.controller.preview_target_size_plan(video)
                self.target_plan_status.setText(plan.explanation)
            else:
                self.target_plan_status.setText("")

            # During dashboard construction the separate Convert page has not
            # been created yet, so this label may not exist when preloaded
            # photos are present in the controller.
            if hasattr(self, "convert_mode_banner"):
                if video:
                    mode, reason = self.controller.check_conversion_mode(
                        video, self.controller.target_container
                    )
                    self.convert_mode_banner.setText(f"{mode.value}: {reason}")
                else:
                    self.convert_mode_banner.setText(
                        self.controller.translator.t("image_support_note")
                    )

            warnings = self.controller.evaluate_active_warnings()
            if warnings:
                self.warning_banner.setText("\n".join(warnings))
                self.warning_banner.setVisible(True)
            else:
                self.warning_banner.setVisible(False)

        def _refresh_export_toggle_caption(self) -> None:
            thai = self.controller.translator.language == "th"
            expanded = self.btn_toggle_export_options.isChecked()
            self.btn_toggle_export_options.setText(
                ("ตั้งค่า Export เพิ่มเติม ▴" if expanded else "ตั้งค่า Export เพิ่มเติม ▾")
                if thai else ("More export settings ▴" if expanded else "More export settings ▾")
            )

        def _on_output_preferences_changed(self, *_args: object) -> bool:
            try:
                self.controller.update_output_preferences(
                    suffix=self.suffix_edit.text(),
                    policy=OutputPolicy(self.output_policy_combo.currentData()),
                    image_format=str(self.image_format_combo.currentData()),
                    image_preset=ImagePreset(self.image_preset_combo.currentData()),
                    image_quality=self.image_quality_spin.value(),
                )
                self.controller.settings.after_export_action = (
                    AfterExportAction.NONE.value if sys.platform != "win32"
                    else AfterExportAction(self.after_action_combo.currentData()).value
                )
                self.controller.settings.skip_larger_outputs = self.skip_larger_check.isChecked()
                self.controller.settings_mgr.save_settings()
                self.image_quality_spin.setEnabled(
                    self.image_preset_combo.currentData() == ImagePreset.CUSTOM.value
                )
                return True
            except (ValueError, OSError) as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))
                return False

        def _confirm_batch_actions(self) -> bool:
            t = self.controller.translator.t
            if self.controller.output_policy in (
                OutputPolicy.REPLACE_OUTPUT,
                OutputPolicy.REPLACE_SOURCE_BACKUP,
            ):
                if QtWidgets.QMessageBox.question(
                    self, "PocketVideo", t("overwrite_warning"),
                    QtWidgets.QMessageBox.StandardButton.Yes |
                    QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No,
                ) != QtWidgets.QMessageBox.StandardButton.Yes:
                    return False
            action = AfterExportAction(self.controller.settings.after_export_action)
            if action != AfterExportAction.NONE:
                if QtWidgets.QMessageBox.question(
                    self, "PocketVideo",
                    t("power_warning", action=action.value),
                    QtWidgets.QMessageBox.StandardButton.Yes |
                    QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No,
                ) != QtWidgets.QMessageBox.StandardButton.Yes:
                    return False
            return True

        def _start_compression_batch(self) -> None:
            if not self.controller.selected_files:
                return
            if not self._sync_current_output_directory():
                return
            if not self._on_output_preferences_changed():
                return
            if not self._confirm_batch_actions():
                return
            selected_op = self.operation_combo.currentData()
            try:
                items = self.controller.enqueue_current_batch(
                    auto_start=False,
                    operation_mode=(
                        OperationMode.CONVERT_TRANSCODE if selected_op == "convert"
                        else OperationMode.COMPRESS
                    ),
                    allow_smart_remux=(selected_op == "convert"),
                )
            except (ValueError, OSError, RuntimeError) as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))
                return
            if not items:
                QtWidgets.QMessageBox.information(
                    self, "PocketVideo", "No jobs queued: all output files already exist."
                )
                return
            self._power_batch_ids = {item.spec.job_id for item in items}
            self._power_batch_action = AfterExportAction(
                self.controller.settings.after_export_action
            )
            self._power_prompt_pending = False
            self._switch_page(2)
            if self.controller.queue_manager:
                self.controller.queue_manager.start()

        def _go_to_convert_on_main(self) -> None:
            self.operation_combo.setCurrentIndex(self.operation_combo.findData("convert"))
            self._switch_page(0)

        def _start_conversion_batch(self) -> None:
            self.operation_combo.setCurrentIndex(self.operation_combo.findData("convert"))
            self._start_compression_batch()

        def _on_job_updated_ui(self, item: QueueItem) -> None:
            row = self._find_or_create_job_row(item.spec.job_id)
            self.queue_table.setItem(row, 0, QtWidgets.QTableWidgetItem(item.spec.job_id))
            self.queue_table.setItem(row, 1, QtWidgets.QTableWidgetItem(item.spec.input_path.name))
            output_path = item.result.output_path if item.result else item.spec.output_path
            dest = str(output_path.parent)
            dest_item = QtWidgets.QTableWidgetItem(dest)
            dest_item.setToolTip(dest)
            dest_item.setData(Qt.ItemDataRole.UserRole, dest)
            self.queue_table.setItem(row, 2, dest_item)
            self.queue_table.setItem(row, 3, QtWidgets.QTableWidgetItem(item.status.value.upper()))
            self.queue_table.setItem(row, 4, QtWidgets.QTableWidgetItem(f"{item.progress.percent:.1f}%"))
            self.queue_table.setItem(
                row, 5, QtWidgets.QTableWidgetItem(
                    f"{item.progress.fps:.1f} fps ({item.progress.speed_multiplier:.2f}x)"
                )
            )
            self.queue_table.setItem(
                row, 6, QtWidgets.QTableWidgetItem(
                    f"{format_duration(item.progress.elapsed_seconds)} / "
                    f"{format_duration(item.progress.eta_seconds)}"
                )
            )
            result_item = QtWidgets.QTableWidgetItem("")
            if item.result:
                result = item.result
                delta = result.space_saved_bytes
                # Positive saved bytes → green, negative saved bytes → red.
                # The neutral zero-difference case inherits the current theme.
                if delta > 0:
                    result_item.setForeground(QtGui.QBrush(QtGui.QColor("#31b878")))
                elif delta < 0:
                    result_item.setForeground(QtGui.QBrush(QtGui.QColor("#ef6868")))
                result_item.setText(
                    f"{format_bytes(result.original_size_bytes)} → "
                    f"{format_bytes(result.output_size_bytes)} "
                    f"(≈{100 * result.output_size_bytes / max(1, result.original_size_bytes):.1f}% remaining)"
                )
            elif item.error_message:
                result_item.setText(item.error_message)
                if ("USB/HDD" in item.error_message
                        and item.spec.job_id not in self._usb_error_alerted):
                    self._usb_error_alerted.add(item.spec.job_id)
                    self.warning_banner.setText(
                        self.controller.translator.t("usb_disconnected")
                    )
                    self.warning_banner.setVisible(True)
            else:
                result_item.setText(item.resource_summary)
            self.queue_table.setItem(row, 7, result_item)
            self.queue_table.setItem(row, 9, QtWidgets.QTableWidgetItem(
                item.started_at.replace("T", " ") if item.started_at else "—"
            ))
            self.queue_table.setItem(row, 10, QtWidgets.QTableWidgetItem(
                item.finished_at.replace("T", " ") if item.finished_at else "—"
            ))
            current_widget = self.queue_table.cellWidget(row, 8)
            # Preserve UI responsiveness: actions need rebuilding only when
            # queue status or final result changes, not on every FFmpeg tick.
            action_signature = (
                item.status.value,
                str(item.result.output_path) if item.result else "",
                bool((item.result.source_backup_path or item.result.input_path).is_file())
                if item.result else False,
            )
            if current_widget and current_widget.property("actionSignature") == action_signature:
                return
            if current_widget:
                self.queue_table.removeCellWidget(row, 8)
            action_widget = QtWidgets.QWidget(self.queue_table)
            action_widget.setProperty("actionSignature", action_signature)
            buttons = QtWidgets.QHBoxLayout(action_widget)
            buttons.setContentsMargins(2, 0, 2, 0)
            buttons.setSpacing(4)
            # Both folder shortcuts exist even before processing begins.
            th = self.controller.translator.language == "th"
            for text_label, target in (
                ("ต้นทาง" if th else "Source", item.spec.input_path),
                ("ปลายทาง" if th else "Output", output_path),
            ):
                button = QtWidgets.QToolButton()
                button.setText(text_label)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                assign(button, "folder", tooltip=str(target), size=14)
                button.clicked.connect(
                    lambda _checked=False, p=target: self._open_job_location(p)
                )
                buttons.addWidget(button)
            more = QtWidgets.QToolButton()
            more.setText("⋯")
            assign(more, "more", tooltip="Open / Compare / Retry", size=14)
            more.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QtWidgets.QMenu(more)
            original = (
                item.result.source_backup_path
                if item.result and item.result.source_backup_path
                else item.spec.input_path
            )
            before_action = menu.addAction(
                "เปิด Before" if th else "Open Before"
            )
            before_action.setIcon(icon("file", more))
            before_action.setEnabled(original.is_file())
            before_action.triggered.connect(
                lambda _checked=False, p=original: self._open_file_path(p)
            )
            after_action = menu.addAction(
                "เปิด After" if th else "Open After"
            )
            after_action.setIcon(icon("file", more))
            after_action.setEnabled(bool(item.result and item.result.output_path.is_file()))
            after_action.triggered.connect(
                lambda _checked=False, p=output_path: self._open_file_path(p)
            )
            compare_action = menu.addAction("เทียบภาพ" if th else "Compare")
            compare_action.setIcon(icon("compare", more))
            compare_action.setEnabled(bool(item.result and item.result.output_path.is_file()))
            if item.result:
                compare_action.triggered.connect(
                    lambda _checked=False, r=item.result: self._show_completed_compare(r)
                )
            if item.status == JobStatus.COMPLETED and item.result:
                menu.addSeparator()
                delete_action = menu.addAction(
                    "ย้ายต้นฉบับลงถังขยะ" if th else "Move Original to Trash"
                )
                try:
                    self._verified_trash_candidate(item)
                except (OSError, ValueError):
                    delete_action.setEnabled(False)
                delete_action.triggered.connect(
                    lambda _checked=False, jid=item.spec.job_id: self._trash_original_job(jid)
                )
            if item.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                menu.addSeparator()
                retry = menu.addAction("ลองใหม่" if th else "Retry")
                retry.setIcon(icon("retry", more))
                retry.triggered.connect(
                    lambda _checked=False, jid=item.spec.job_id: self._retry_job(jid)
                )
            more.setMenu(menu)
            buttons.addWidget(more)
            self.queue_table.setCellWidget(row, 8, action_widget)

        def _open_job_location(self, target: Path) -> None:
            """Reveal a finished file or open its folder while still queued."""
            try:
                if target.is_file():
                    reveal_file_in_location(target)
                elif target.parent.is_dir():
                    if not QtGui.QDesktopServices.openUrl(
                        QtCore.QUrl.fromLocalFile(str(target.parent))
                    ):
                        raise OSError(f"Cannot open directory: {target.parent}")
                else:
                    raise FileNotFoundError(f"Directory unavailable: {target.parent}")
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))

        def _retry_job(self, job_id: str) -> None:
            if self.controller.queue_manager:
                self.controller.queue_manager.retry_job(job_id)

        def _open_file_path(self, path: Path) -> None:
            if not path.is_file():
                QtWidgets.QMessageBox.warning(
                    self, "PocketVideo", "ไม่พบไฟล์ • ตรวจสอบไดรฟ์ USB/HDD"
                )
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

        def _open_output_file(self, result: JobResultComparison) -> None:
            self._open_file_path(result.output_path)

        def _open_output_folder(self, result: JobResultComparison) -> None:
            try:
                reveal_file_in_location(result.output_path)
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))

        def _show_completed_compare(self, result: JobResultComparison) -> None:
            original = result.source_backup_path or result.input_path
            if not original.is_file() or not result.output_path.is_file():
                QtWidgets.QMessageBox.warning(self, "PocketVideo", "ไฟล์ Before/After ไม่อยู่แล้ว")
                return
            if original.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                dlg = CompletedVideoCompareDialog(result, self.controller.binaries, self)
            else:
                if not self.controller.binaries:
                    QtWidgets.QMessageBox.warning(self, "PocketVideo", "FFmpeg is unavailable.")
                    return
                from pocketvideo.ui.video_compare import SynchronizedVideoCompareDialog
                dlg = SynchronizedVideoCompareDialog(
                    original, result.output_path, self.controller.binaries, self
                )
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
            if (
                self._power_batch_action == AfterExportAction.NONE
                or not self._power_batch_ids
                or self._power_prompt_pending
                or self.controller.queue_manager is None
            ):
                return
            statuses = {
                x.spec.job_id: x.status for x in self.controller.queue_manager.items()
            }
            if not self._power_batch_ids.issubset(statuses):
                return
            if any(x in (JobStatus.FAILED, JobStatus.CANCELLED) for x in statuses.values()):
                # A failed export disarms power actions, including on a later
                # Retry Failed. Starting a NEW batch requires confirmation again.
                self._power_batch_action = AfterExportAction.NONE
                self._power_batch_ids.clear()
                return
            # Never power off after a failed, skipped or cancelled job, or while
            # any older queue job is still active.
            if (
                all(x == JobStatus.COMPLETED for x in statuses.values())
                and self.controller.queue_manager.is_idle()
            ):
                self._power_verified_outputs = [
                    x.result.output_path
                    for x in self.controller.queue_manager.items()
                    if x.result is not None
                ]
                self._power_prompt_pending = True
                QtCore.QTimer.singleShot(0, self._show_post_export_countdown)

        def _show_post_export_countdown(self) -> None:
            action = self._power_batch_action
            self._power_batch_ids = set()
            self._power_batch_action = AfterExportAction.NONE
            if action == AfterExportAction.NONE:
                return
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("PocketVideo — After Export")
            dialog.setMinimumWidth(440)
            layout = QtWidgets.QVBoxLayout(dialog)
            label = QtWidgets.QLabel()
            label.setWordWrap(True)
            layout.addWidget(label)
            cancel = QtWidgets.QPushButton(
                self.controller.translator.t("cancel_power")
            )
            cancel.clicked.connect(dialog.reject)
            layout.addWidget(cancel)
            remaining = [30]
            def tick() -> None:
                label.setText(
                    self.controller.translator.t(
                        "power_countdown", action=action.value,
                        seconds=remaining[0]
                    )
                )
                if remaining[0] <= 0:
                    dialog.accept()
                remaining[0] -= 1
            timer = QtCore.QTimer(dialog)
            timer.timeout.connect(tick)
            tick()
            timer.start(1000)
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                if not self._power_verified_outputs or any(
                    not p.is_file() for p in self._power_verified_outputs
                ):
                    QtWidgets.QMessageBox.warning(
                        self, "PocketVideo", "Exported files are no longer accessible; power action cancelled."
                    )
                    return
                try:
                    run_after_export(action, user_confirmed=True)
                except (OSError, PermissionError, RuntimeError) as exc:
                    QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))

        def _persist_and_relay_completed(self, result: JobResultComparison) -> None:
            # Preserve the controller persistence callback; a previous UI signal
            # assignment silently replaced it and broke ALL lifetime statistics.
            try:
                self.controller._on_job_completed(result)
            except Exception as exc:
                self.signals.history_error.emit(str(exc))
            self.signals.job_completed.emit(result)

        def _on_history_error_ui(self, error: str) -> None:
            QtWidgets.QMessageBox.warning(
                self, "PocketVideo",
                "Export completed, but savings history could not be saved: " + error,
            )

        def _on_job_completed_ui(self, _comparison: JobResultComparison) -> None:
            self._refresh_history_table()

        def _restyle_history_summary(self) -> None:
            saved = getattr(self, "_last_saved_bytes", 0)
            palette = self._history_palette()
            color = (palette.success if saved > 0 else
                     palette.danger if saved < 0 else palette.text_primary)
            self.history_total_value.setStyleSheet(
                f"font-size: 29px; font-weight: 700; color: {color};"
            )
            self.history_ratio_value.setStyleSheet(
                f"font-weight: 600; color: {color};"
            )

        def _history_saved_label(self, value: int) -> str:
            return ("-" if value < 0 else "") + format_bytes(abs(value))

        def _history_palette(self):
            return DARK_PALETTE if self.controller.settings.theme == "dark" else LIGHT_PALETTE

        def _refresh_history_table(self) -> None:
            """Render immutable completed-job snapshots; no source needed."""
            t = self.controller.translator.t
            stats = self.controller.settings_mgr.load_savings_stats()
            saved = stats.net_saved_bytes
            self._last_saved_bytes = saved
            palette = self._history_palette()
            indicator = (
                palette.success if saved > 0 else
                palette.danger if saved < 0 else palette.text_primary
            )
            self.history_total_value.setText(self._history_saved_label(saved))
            self.history_total_value.setStyleSheet(
                f"font-size: 29px; font-weight: 700; color: {indicator};"
            )
            self.history_jobs_value.setText(
                f"{t('history_lifetime_jobs')}: {stats.completed_jobs:,}"
            )
            self.history_ratio_value.setText(
                f"{t('history_lifetime_ratio')}: {stats.reduction_percent:+.1f}%"
            )
            self.history_ratio_value.setStyleSheet(
                f"font-weight: 600; color: {indicator};"
            )

            history = self.controller.settings_mgr.load_history()
            self.history_table.setRowCount(len(history))
            for row, entry in enumerate(history):
                output_ok = entry.output_path.is_file()
                original_file = (
                    entry.source_backup_path if (
                        entry.source_backup_path and entry.source_backup_path.is_file()
                    ) else entry.input_path
                )
                original_ok = original_file.is_file()
                if original_ok and output_ok:
                    state = t("history_files_present")
                elif not original_ok and not output_ok:
                    state = t("history_both_missing")
                elif not original_ok:
                    state = t("history_original_missing")
                else:
                    state = t("history_output_missing")
                values = (
                    entry.output_path.name,
                    format_bytes(entry.original_size_bytes),
                    format_bytes(entry.output_size_bytes),
                    self._history_saved_label(entry.space_saved_bytes),
                    f"≈{100 * entry.output_size_bytes / max(1, entry.original_size_bytes):.1f}%",
                    f"{entry.original_resolution} → {entry.output_resolution}",
                    f"{entry.original_codec} → {entry.output_codec}",
                    state,
                )
                for col, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setToolTip(
                        f"Before: {original_file}\nAfter: {entry.output_path}\n"
                        "Historical measurement remains available even if files were deleted."
                    )
                    if col in (3, 4):
                        item.setForeground(QtGui.QBrush(QtGui.QColor(
                            palette.success if entry.space_saved_bytes > 0
                            else palette.danger if entry.space_saved_bytes < 0
                            else palette.text_primary
                        )))
                    elif col == 7 and not (original_ok and output_ok):
                        item.setForeground(QtGui.QBrush(QtGui.QColor(palette.warning)))
                    self.history_table.setItem(row, col, item)
                folder_btn = QtWidgets.QToolButton(self.history_table)
                folder_btn.setText(t("history_actions"))
                folder_btn.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                )
                assign(folder_btn, "folder", size=14)
                folder_btn.setPopupMode(
                    QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
                )
                menu = QtWidgets.QMenu(folder_btn)
                for label, target in (("Before", original_file),
                                      ("After", entry.output_path)):
                    act = menu.addAction(label)
                    act.setEnabled(target.parent.is_dir())
                    act.triggered.connect(
                        lambda _checked=False, p=target: self._open_job_location(p)
                    )
                folder_btn.setMenu(menu)
                self.history_table.setCellWidget(row, 8, folder_btn)

        def _copy_history_stats(self) -> None:
            stats = self.controller.settings_mgr.load_savings_stats()
            summary = share_summary(
                stats, self.controller.translator.language,
                self._history_saved_label(stats.net_saved_bytes),
            )
            QtGui.QGuiApplication.clipboard().setText(summary)

        def _copy_history_image(self) -> None:
            """Copy a branded 1200x630 scorecard to paste into social posts."""
            stats = self.controller.settings_mgr.load_savings_stats()
            palette = self._history_palette()
            img = QtGui.QPixmap(1200, 630)
            img.fill(QtGui.QColor(palette.bg_primary))
            painter = QtGui.QPainter(img)
            try:
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
                gradient = QtGui.QLinearGradient(0, 0, 1200, 630)
                gradient.setColorAt(0.0, QtGui.QColor("#12203F"))
                gradient.setColorAt(1.0, QtGui.QColor("#29205C"))
                painter.setBrush(QtGui.QBrush(gradient))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QtCore.QRectF(24, 24, 1152, 582), 38, 38)
                painter.drawPixmap(76, 62, app_icon(88).pixmap(88, 88))
                painter.setPen(QtGui.QColor("#F8FAFC"))
                painter.setFont(QtGui.QFont("Segoe UI", 26, QtGui.QFont.Weight.Bold))
                painter.drawText(QtCore.QRect(190, 74, 895, 76),
                                 Qt.AlignmentFlag.AlignVCenter, "PocketVideo")
                painter.setFont(QtGui.QFont("Segoe UI", 23))
                painter.setPen(QtGui.QColor("#CBD5E1"))
                painter.drawText(QtCore.QRect(82, 162, 1000, 56),
                                 Qt.AlignmentFlag.AlignVCenter,
                                 self.controller.translator.t("history_lifetime_title"))
                saved = stats.net_saved_bytes
                painter.setPen(QtGui.QColor(
                    "#36DEA4" if saved > 0 else "#FF8E8E" if saved < 0 else "#FFFFFF"
                ))
                painter.setFont(QtGui.QFont("Segoe UI", 58, QtGui.QFont.Weight.Bold))
                painter.drawText(QtCore.QRect(76, 220, 1040, 122),
                                 Qt.AlignmentFlag.AlignVCenter,
                                 self._history_saved_label(saved))
                painter.setPen(QtGui.QColor("#E2E8F0"))
                painter.setFont(QtGui.QFont("Segoe UI", 24))
                painter.drawText(QtCore.QRect(82, 344, 1050, 52),
                                 Qt.AlignmentFlag.AlignVCenter,
                                 f"{stats.completed_jobs:,} files • "
                                 f"{stats.reduction_percent:+.1f}% size reduction")
                painter.setFont(QtGui.QFont("Segoe UI", 23))
                painter.drawText(QtCore.QRect(82, 423, 1040, 62),
                                 Qt.AlignmentFlag.AlignVCenter,
                                 self.controller.translator.t("history_lifetime_quote"))
                painter.setPen(QtGui.QColor("#A6B5D6"))
                painter.setFont(QtGui.QFont("Segoe UI", 14))
                painter.drawText(QtCore.QRect(82, 533, 1035, 32),
                                 Qt.AlignmentFlag.AlignVCenter,
                                 "Based on before/after sizes • github.com/Apichetfirm/PocketVideo")
            finally:
                painter.end()
            QtGui.QGuiApplication.clipboard().setPixmap(img)

        def _clear_queue_history(self) -> None:
            manager = self.controller.queue_manager
            if manager is None:
                return
            finished = [x for x in manager.items() if x.status in (
                JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED
            )]
            if not finished:
                return
            th = self.controller.translator.language == "th"
            message = (
                f"ล้างรายการที่จบแล้ว {len(finished)} งานออกจากคิว?\\n"
                "ไม่ลบไฟล์หรือสถิติสะสม"
                if th else
                f"Clear {len(finished)} finished queue entries?\\n"
                "Files and lifetime statistics will be kept."
            )
            if QtWidgets.QMessageBox.question(
                self, "PocketVideo", message,
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            ) != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            manager.clear_finished()
            self.queue_table.setRowCount(0)
            for item in manager.items():
                self._on_job_updated_ui(item)

        def _clear_visible_history(self) -> None:
            th = self.controller.translator.language == "th"
            message = (
                "ล้างรายการประวัติทั้งหมด? สถิติสะสมจะยังอยู่ แต่รายการเก่าจะกู้จากแอปไม่ได้"
                if th else
                "Clear visible history? Lifetime totals remain, but old history rows cannot be recovered in the app."
            )
            if QtWidgets.QMessageBox.question(
                self, "PocketVideo", message,
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            ) != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                self.controller.settings_mgr.clear_history()
            except OSError as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))
                return
            self._refresh_history_table()

        def _verified_trash_candidate(self, item: QueueItem) -> Path:
            if item.result is None or item.status != JobStatus.COMPLETED:
                raise ValueError("Only successfully verified exports can delete originals.")
            path = verified_original_for_trash(
                item.result, item.spec.source_fingerprint,
                item.verified_output_fingerprint,
            )
            # Never remove a file still being read as input or required as
            # another output in this queue.
            manager = self.controller.queue_manager
            if manager is not None:
                for other in manager.items():
                    if other.spec.job_id == item.spec.job_id:
                        continue
                    if (other.status not in (
                        JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED
                    ) and other.spec.input_path.resolve() == path.resolve()):
                        raise ValueError("Another active or pending job uses this original.")
                    if (other.status == JobStatus.COMPLETED and other.result
                            and other.result.output_path.resolve() == path.resolve()):
                        raise ValueError("This original is also an exported result of another job.")
            return path

        def _trash_original_job(self, job_id: str) -> None:
            manager = self.controller.queue_manager
            item = manager.get_item(job_id) if manager else None
            if not item or item.status != JobStatus.COMPLETED or not item.result:
                return
            try:
                original = self._verified_trash_candidate(item)
            except (ValueError, OSError) as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))
                return
            th = self.controller.translator.language == "th"
            message = (
                f"ย้ายต้นฉบับลงถังขยะ?\\n{original}\\n"
                f"ผลลัพธ์ที่ตรวจแล้ว: {item.result.output_path}\\nล้างถังขยะจึงจะได้พื้นที่ว่างคืน"
                if th else
                f"Move this original to the OS Trash/Recycle Bin?\\n{original}\\n"
                f"Verified output: {item.result.output_path}\\nEmpty the Trash to reclaim disk space."
            )
            if QtWidgets.QMessageBox.question(
                self, "PocketVideo", message,
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            ) != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                self._verified_trash_candidate(item)
                if not QtCore.QFile.moveToTrash(str(original)):
                    raise OSError("Could not move original to Trash. File was kept.")
            except (ValueError, OSError) as exc:
                QtWidgets.QMessageBox.warning(self, "PocketVideo", str(exc))
                return
            self._on_job_updated_ui(item)
            self._refresh_history_table()

        def _trash_all_originals(self) -> None:
            manager = self.controller.queue_manager
            if manager is None:
                return
            eligible = []
            seen = set()
            for item in manager.items():
                if item.status != JobStatus.COMPLETED or item.result is None:
                    continue
                try:
                    path = self._verified_trash_candidate(item)
                except (OSError, ValueError):
                    continue
                key = str(path.resolve()).casefold()
                if key not in seen:
                    seen.add(key)
                    eligible.append((item, path))
            th = self.controller.translator.language == "th"
            if not eligible:
                QtWidgets.QMessageBox.information(
                    self, "PocketVideo",
                    "ไม่มีต้นฉบับที่ลบได้อย่างปลอดภัย"
                    if th else "No safely verified originals available.",
                )
                return
            size = sum(path.stat().st_size for _item, path in eligible)
            message = (
                f"ย้าย {len(eligible)} ไฟล์ลงถังขยะ?\\n"
                f"ขนาดต้นฉบับรวม: {format_bytes(size)}\\n"
                "ไฟล์ที่ล้มเหลวจะไม่ถูกแตะต้อง • ล้างถังขยะจึงจะคืนพื้นที่ว่าง"
                if th else
                f"Move {len(eligible)} originals to OS Trash/Recycle Bin?\\n"
                f"Total original size: {format_bytes(size)}\\n"
                "Failed/unverified exports will not be touched. Empty Trash to reclaim space."
            )
            if QtWidgets.QMessageBox.question(
                self, "PocketVideo", message,
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            ) != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            removed = 0
            errors = []
            for item, path in eligible:
                try:
                    self._verified_trash_candidate(item)
                    if not QtCore.QFile.moveToTrash(str(path)):
                        raise OSError("OS Trash rejected file")
                    removed += 1
                    self._on_job_updated_ui(item)
                except (ValueError, OSError) as exc:
                    errors.append(f"{path.name}: {exc}")
            self._refresh_history_table()
            QtWidgets.QMessageBox.information(
                self, "PocketVideo",
                f"{removed}/{len(eligible)} originals moved to Trash.\\n"
                + ("\\n".join(errors[:8]) if errors else ""),
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
            value = max(10, min(100, int(value)))
            idx = self.cpu_cap_combo.findData(value)
            if idx < 0:
                idx = self.cpu_cap_combo.findData("custom")
            with QtCore.QSignalBlocker(self.cpu_cap_combo), QtCore.QSignalBlocker(self.cpu_custom_spin):
                self.cpu_custom_spin.setValue(value)
                self.cpu_cap_combo.setCurrentIndex(idx)
            self.cpu_custom_spin.setVisible(
                self.cpu_cap_combo.currentData() == "custom"
            )

        def _on_cpu_cap_choice_changed(self, *_args: object) -> None:
            self.cpu_custom_spin.setVisible(
                self.cpu_cap_combo.currentData() == "custom"
            )
            self._on_resource_controls_edited()

        def _on_resource_mode_changed(self) -> None:
            mode_val = self.res_mode_combo.currentData()
            if mode_val:
                mode = ResourceMode(str(mode_val))
                if mode == ResourceMode.CUSTOM:
                    return
                limits = self.controller.apply_resource_mode(mode)

                for widget in (self.cpu_cap_combo, self.cpu_custom_spin,
                               self.threads_spin, self.concurrent_spin,
                               self.chk_gpu_enable, self.gpu_backend_combo):
                    widget.blockSignals(True)
                try:

                    self._set_cpu_cap_selection(limits.cpu_hard_cap_percent)
                    self.threads_spin.setValue(limits.cpu_threads)
                    self.concurrent_spin.setValue(limits.max_concurrent_jobs)
                    self.chk_gpu_enable.setChecked(limits.enable_gpu_encoding)
                    self.gpu_backend_combo.setCurrentIndex(
                        max(0, self.gpu_backend_combo.findData(limits.preferred_gpu_encoder))
                    )
                finally:

                    for widget in (self.cpu_cap_combo, self.cpu_custom_spin,
                                   self.threads_spin, self.concurrent_spin,
                                   self.chk_gpu_enable, self.gpu_backend_combo):
                        widget.blockSignals(False)

        def _on_resource_controls_edited(self, *_args: object) -> None:
            self.controller.update_resource_controls(

                cpu_hard_cap_percent=(
                    self.cpu_custom_spin.value()
                    if self.cpu_cap_combo.currentData() == "custom"
                    else int(self.cpu_cap_combo.currentData())
                ),
                cpu_threads=self.threads_spin.value(),
                max_concurrent_jobs=self.concurrent_spin.value(),
                enable_gpu_encoding=self.chk_gpu_enable.isChecked(),
                preferred_gpu_encoder=str(self.gpu_backend_combo.currentData()),
            )
            with QtCore.QSignalBlocker(self.res_mode_combo):
                idx = self.res_mode_combo.findData(ResourceMode.CUSTOM.value)
                self.res_mode_combo.setCurrentIndex(idx)
