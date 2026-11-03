"""Synchronized Before/After playback with a floating, zoomable detail inspector.

Both panes use the *real* exported video files. One shared transport drives
both players and corrects drift. Sound is muted to avoid duplicate playback.
The zoom window extracts same-timestamp frames off the UI thread with FFmpeg,
so the original resolution can be inspected independently of display size.
"""
from __future__ import annotations

import threading
import tempfile
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from pocketvideo.media.encoder import extract_representative_frame
from pocketvideo.media.images import SUPPORTED_IMAGE_EXTENSIONS, thumbnail_png


def _duration_text(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds)) // 1000
    return f"{seconds // 3600:02}:{(seconds // 60) % 60:02}:{seconds % 60:02}"


class ZoomDetailDialog(QtWidgets.QDialog):
    """Floating, pannable original-pixel inspection at one shared timestamp."""

    frames_ready = Signal(str, str)
    frames_failed = Signal(str)

    def __init__(self, before: Path, after: Path, binaries, position_ms: int, parent=None, before_offset_ms: int = 0):
        super().__init__(parent)
        self.before_path = Path(before)
        self.after_path = Path(after)
        self.binaries = binaries
        self.position_ms = max(0, int(position_ms))
        self.before_offset_ms = max(0, int(before_offset_ms))
        self._scratch = tempfile.TemporaryDirectory(prefix="pocketvideo_detail_zoom_")
        self._generation = 0
        self.setWindowTitle("รายละเอียดภาพ • Before / After")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.resize(820, 470)
        self.frames_ready.connect(self._show_frames)
        self.frames_failed.connect(self._show_error)

        layout = QtWidgets.QVBoxLayout(self)
        control = QtWidgets.QHBoxLayout()
        self.zoom_label = QtWidgets.QLabel("ซูม 200%")
        self.zoom_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(100, 500)
        self.zoom_slider.setSingleStep(25)
        self.zoom_slider.setValue(200)
        self.zoom_slider.valueChanged.connect(self._update_zoom)
        self.status_label = QtWidgets.QLabel("กำลังอ่านเฟรมจากไฟล์จริง…")
        control.addWidget(self.zoom_label)
        control.addWidget(self.zoom_slider, 1)
        layout.addLayout(control)

        views = QtWidgets.QHBoxLayout()
        self.views: list[QtWidgets.QGraphicsView] = []
        self.scenes: list[QtWidgets.QGraphicsScene] = []
        self.pixmap_items: list[QtWidgets.QGraphicsPixmapItem] = []
        for caption in ("Before • ต้นฉบับ", "After • ผลลัพธ์"):
            wrapper = QtWidgets.QVBoxLayout()
            wrapper.addWidget(QtWidgets.QLabel(caption))
            view = QtWidgets.QGraphicsView()
            view.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
            view.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
            view.setMinimumWidth(200)
            scene = QtWidgets.QGraphicsScene(view)
            item = QtWidgets.QGraphicsPixmapItem()
            scene.addItem(item)
            view.setScene(scene)
            self.views.append(view)
            self.scenes.append(scene)
            self.pixmap_items.append(item)
            wrapper.addWidget(view, 1)
            views.addLayout(wrapper, 1)
        layout.addLayout(views, 1)
        layout.addWidget(self.status_label)
        QtCore.QTimer.singleShot(0, self._extract_frame_pair)

    def _extract_frame_pair(self) -> None:
        self._generation += 1
        index = self._generation
        timestamp = self.position_ms / 1000.0
        before_image = Path(self._scratch.name) / f"before_{index}.png"
        after_image = Path(self._scratch.name) / f"after_{index}.png"

        def background() -> None:
            try:
                if self.before_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    thumbnail_png(self.before_path, before_image, (4096, 4096))
                    thumbnail_png(self.after_path, after_image, (4096, 4096))
                else:
                    extract_representative_frame(
                        self.before_path, before_image, self.binaries,
                        timestamp_seconds=timestamp + self.before_offset_ms / 1000.0,
                    )
                    extract_representative_frame(
                        self.after_path, after_image, self.binaries,
                        timestamp_seconds=timestamp,
                    )
                self.frames_ready.emit(str(before_image), str(after_image))
            except Exception as exc:
                self.frames_failed.emit(str(exc))

        threading.Thread(target=background, name="PocketVideoZoomFrames", daemon=True).start()

    def _show_frames(self, before_file: str, after_file: str) -> None:
        for path, item, view in zip(
            (before_file, after_file), self.pixmap_items, self.views
        ):
            pixmap = QtGui.QPixmap(path)
            if pixmap.isNull():
                self.status_label.setText("ไม่สามารถโหลดเฟรมได้")
                return
            item.setPixmap(pixmap)
            item.setScale(self.zoom_slider.value() / 100.0)
            view.scene().setSceneRect(item.sceneBoundingRect())
            view.centerOn(item)
        self.status_label.setText(
            f"เฟรมเวลา {_duration_text(self.position_ms)} • ลากภาพเพื่อดูรายละเอียด"
        )

    def _update_zoom(self, value: int) -> None:
        self.zoom_label.setText(f"ซูม {value}%")
        for item, view in zip(self.pixmap_items, self.views):
            item.setScale(value / 100.0)
            view.scene().setSceneRect(item.sceneBoundingRect())
            view.centerOn(item)

    def _show_error(self, error: str) -> None:
        self.status_label.setText(f"ไม่สามารถอ่านเฟรม: {error}")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._scratch.cleanup()
        super().closeEvent(event)


class SynchronizedVideoCompareDialog(QtWidgets.QDialog):
    """Shared transport for simultaneous original and compressed playback."""

    def __init__(self, before: Path, after: Path, binaries, parent=None, before_offset_ms: int = 0):
        super().__init__(parent)
        self.before_offset_ms = max(0, int(before_offset_ms))
        self.before_path = Path(before).resolve()
        self.after_path = Path(after).resolve()
        self.binaries = binaries
        if not self.before_path.is_file() or not self.after_path.is_file():
            raise FileNotFoundError("Before/After file unavailable; reconnect the original storage.")
        self.setWindowTitle("PocketVideo — เทียบวิดีโอก่อน / หลัง")
        self.resize(1140, 660)
        self._seeking = False
        self._zoom_dialogs: list[ZoomDetailDialog] = []
        self._players: list[QMediaPlayer] = []
        self._outputs: list[QAudioOutput] = []

        layout = QtWidgets.QVBoxLayout(self)
        description = QtWidgets.QLabel("เล่นพร้อมกัน • หยุด/เลื่อนเวลาได้พร้อมกัน • ซูมดูรายละเอียดเฟรมจริง")
        description.setObjectName("MutedLabel")
        layout.addWidget(description)

        panes = QtWidgets.QHBoxLayout()
        self.video_widgets: list[QVideoWidget] = []
        for caption, source in (("BEFORE • ต้นฉบับ", self.before_path), ("AFTER • ผลลัพธ์", self.after_path)):
            column = QtWidgets.QVBoxLayout()
            label = QtWidgets.QLabel(caption)
            label.setObjectName("SubheadingLabel")
            column.addWidget(label)
            video = QVideoWidget()
            video.setMinimumSize(320, 220)
            video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
            column.addWidget(video, 1)
            filename = QtWidgets.QLabel(source.name)
            filename.setObjectName("MutedLabel")
            filename.setToolTip(str(source))
            column.addWidget(filename)
            panes.addLayout(column, 1)
            player = QMediaPlayer(self)
            sound = QAudioOutput(self)
            sound.setMuted(True)
            player.setAudioOutput(sound)
            player.setVideoOutput(video)
            player.setSource(QtCore.QUrl.fromLocalFile(str(source)))
            player.errorOccurred.connect(self._on_playback_error)
            self._players.append(player)
            self._outputs.append(sound)
            self.video_widgets.append(video)
        layout.addLayout(panes, 1)

        transport = QtWidgets.QHBoxLayout()
        self.play_button = QtWidgets.QPushButton("▶ เล่นพร้อมกัน")
        self.play_button.setObjectName("PrimaryStartButton")
        self.play_button.clicked.connect(self.toggle_play)
        self.seek_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(self._pause_seek)
        self.seek_slider.sliderReleased.connect(self._finish_seek)
        self.clock = QtWidgets.QLabel("00:00:00 / 00:00:00")
        self.zoom_button = QtWidgets.QPushButton("ขยายดูรายละเอียด 100–500%")
        self.zoom_button.clicked.connect(self.open_detail_zoom)
        transport.addWidget(self.play_button)
        transport.addWidget(self.seek_slider, 1)
        transport.addWidget(self.clock)
        transport.addWidget(self.zoom_button)
        layout.addLayout(transport)
        self.status_label = QtWidgets.QLabel("เริ่มเล่นวิดีโอได้เลย (เสียงปิดอยู่เพื่อไม่ให้ซ้อนกัน)")
        self.status_label.setObjectName("MutedLabel")
        layout.addWidget(self.status_label)

        # Qt Multimedia can report different durations while loading sources.
        # We seek both to the same millisecond and limit the scrubber to the
        # shorter duration as soon as both sources have reported one.
        for player in self._players:
            player.durationChanged.connect(self._refresh_duration)
        self._players[0].positionChanged.connect(self._sync_position)
        self._players[0].playbackStateChanged.connect(self._refresh_play_button)

    @property
    def before_player(self) -> QMediaPlayer:
        return self._players[0]

    @property
    def after_player(self) -> QMediaPlayer:
        return self._players[1]

    def _refresh_duration(self, *_args: object) -> None:
        durations = [p.duration() for p in self._players if p.duration() > 0]
        if durations:
            all_durations = [p.duration() for p in self._players]
            if len(durations) == 2:
                max_ms = min(max(0, all_durations[0] - self.before_offset_ms), all_durations[1])
            else:
                max_ms = max(0, durations[0] - self.before_offset_ms)
            self.seek_slider.setMaximum(max_ms)
            self.clock.setText(
                f"{_duration_text(self.before_player.position())} / {_duration_text(max_ms)}"
            )

    def _sync_position(self, position: int) -> None:
        if self._seeking:
            return
        position = max(0, position - self.before_offset_ms)
        if not self.seek_slider.isSliderDown():
            with QtCore.QSignalBlocker(self.seek_slider):
                self.seek_slider.setValue(position)
        self.clock.setText(
            f"{_duration_text(position)} / {_duration_text(self.seek_slider.maximum())}"
        )
        if self.before_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if abs(self.after_player.position() - position) > 220:
                self.after_player.setPosition(position)
        if self.seek_slider.maximum() > 0 and position >= self.seek_slider.maximum():
            self.pause()

    def _on_playback_error(self, _error, error_string: str) -> None:
        self.status_label.setText(
            "ตัวเล่นวิดีโอไม่รองรับไฟล์นี้ หรือไฟล์ถูกถอดออก: " + error_string
        )

    def _refresh_play_button(self, *_args: object) -> None:
        running = self.before_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("⏸ หยุดชั่วคราว" if running else "▶ เล่นพร้อมกัน")

    def toggle_play(self) -> None:
        if self.before_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        position = max(0, self.before_player.position() - self.before_offset_ms)
        self.after_player.setPosition(position)
        if self.before_offset_ms and self.before_player.position() < self.before_offset_ms:
            self.before_player.setPosition(self.before_offset_ms)
        self.after_player.play()
        self.before_player.play()

    def pause(self) -> None:
        for player in self._players:
            player.pause()

    def seek_to(self, milliseconds: int) -> None:
        milliseconds = max(0, int(milliseconds))
        self.before_player.setPosition(milliseconds + self.before_offset_ms)
        self.after_player.setPosition(milliseconds)

    def _pause_seek(self) -> None:
        self._seeking = True
        self._was_playing = self.before_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.pause()

    def _finish_seek(self) -> None:
        self.seek_to(self.seek_slider.value())
        self._seeking = False
        if getattr(self, "_was_playing", False):
            self.play()

    def open_detail_zoom(self) -> None:
        dialog = ZoomDetailDialog(
            self.before_path, self.after_path, self.binaries,
            max(0, self.before_player.position() - self.before_offset_ms), self,
            before_offset_ms=self.before_offset_ms,
        )
        self._zoom_dialogs.append(dialog)
        dialog.finished.connect(lambda _code, d=dialog: self._forget_zoom(d))
        dialog.show()

    def _forget_zoom(self, dialog: ZoomDetailDialog) -> None:
        if dialog in self._zoom_dialogs:
            self._zoom_dialogs.remove(dialog)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        for player in self._players:
            player.stop()
            player.setSource(QtCore.QUrl())
        for dialog in tuple(self._zoom_dialogs):
            dialog.close()
        super().closeEvent(event)
