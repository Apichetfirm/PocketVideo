"""Queue layout, timestamps, source/output shortcuts and result-color regression tests."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pocketvideo.core.config import SettingsManager
from pocketvideo.core.models import (
    EncodingJobSpec, JobProgress, JobResultComparison, JobStatus, OperationMode,
)
from pocketvideo.queue.manager import QueueItem, QueueManager
from pocketvideo.ui.controller import AppController
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE

if PYSIDE6_AVAILABLE:
    from PySide6 import QtWidgets, QtCore
    from pocketvideo.ui.main_window import PocketVideoMainWindow


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 required")
class CustomQueueTests(unittest.TestCase):
    def test_column_visibility_width_order_and_sidebar_persist(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            settings = SettingsManager(root / "settings")
            window = PocketVideoMainWindow(AppController(settings_manager=settings))
            try:
                self.assertEqual(window.queue_table.columnCount(), 11)
                self.assertTrue(window.queue_table.isColumnHidden(0))
                self.assertFalse(window.queue_table.isColumnHidden(1))
                self.assertFalse(window.queue_table.isColumnHidden(9))
                self.assertFalse(window.queue_table.isColumnHidden(10))
                window._queue_column_actions[0].setChecked(True)
                window._queue_column_actions[6].setChecked(True)
                window._queue_column_actions[2].setChecked(False)
                header = window.queue_table.horizontalHeader()
                header.resizeSection(9, 223)
                header.moveSection(header.visualIndex(9), 0)
                window.main_splitter.setSizes([180, 900])
                window._save_sidebar_width()
                self.assertIn(0, settings.settings.queue_visible_columns)
                self.assertEqual(settings.settings.queue_column_widths["9"], 223)
            finally:
                window.close()
                app.processEvents()

            reloaded = SettingsManager(root / "settings")
            reloaded.load_settings()
            another = PocketVideoMainWindow(AppController(settings_manager=reloaded))
            try:
                self.assertFalse(another.queue_table.isColumnHidden(0))
                self.assertTrue(another.queue_table.isColumnHidden(2))
                self.assertEqual(another.queue_table.columnWidth(9), 223)
                self.assertEqual(another.queue_table.horizontalHeader().logicalIndex(0), 9)
                self.assertGreaterEqual(reloaded.settings.queue_sidebar_width, 140)
            finally:
                another.close()
                app.processEvents()

    def test_queue_timestamps_color_and_queued_folder_shortcuts(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "phone.mp4"
            source.write_bytes(b"input")
            outfolder = root / "Export" / "Camera"
            outfolder.mkdir(parents=True)
            out = outfolder / "phone_small.mp4"
            spec = EncodingJobSpec(
                job_id="job-color", input_path=source, output_path=out,
            )
            item = QueueItem(spec=spec, status=JobStatus.PROCESSING,
                             started_at="2026-11-04T10:22:00+07:00")
            window = PocketVideoMainWindow(
                AppController(settings_manager=SettingsManager(root / "config"))
            )
            try:
                window._on_job_updated_ui(item)
                self.assertIn("10:22", window.queue_table.item(0, 9).text())
                self.assertEqual(window.queue_table.item(0, 10).text(), "—")
                actions = window.queue_table.cellWidget(0, 8)
                buttons = actions.findChildren(QtWidgets.QToolButton)
                self.assertEqual(len(buttons), 3)
                with mock.patch("pocketvideo.ui.main_window.reveal_file_in_location") as reveal:
                    buttons[0].click()
                    reveal.assert_called_once_with(source)
                with mock.patch.object(QtWidgets.QMessageBox, "warning") as warning, mock.patch(
                    "pocketvideo.ui.main_window.QtGui.QDesktopServices.openUrl",
                    return_value=True,
                ) as folder_open:
                    buttons[1].click()
                    warning.assert_not_called()
                    folder_open.assert_called_once()

                out.write_bytes(b"output")
                item.status = JobStatus.COMPLETED
                item.finished_at = "2026-11-04T10:24:00+07:00"
                item.result = JobResultComparison(
                    job_id=spec.job_id,
                    input_path=source, output_path=out,
                    operation_mode=OperationMode.COMPRESS,
                    original_size_bytes=100, output_size_bytes=80,
                    space_saved_bytes=20, reduction_percent=20.0,
                    original_resolution="320x240", output_resolution="320x240",
                    original_codec="h264", output_codec="hevc",
                    processing_duration_seconds=120,
                )
                window._on_job_updated_ui(item)
                green = window.queue_table.item(0, 7).foreground().color().name()
                self.assertEqual(green, "#31b878")
                self.assertIn("10:24", window.queue_table.item(0, 10).text())
                item.result.space_saved_bytes = -20
                item.result.reduction_percent = -20.0
                window._on_job_updated_ui(item)
                red = window.queue_table.item(0, 7).foreground().color().name()
                self.assertEqual(red, "#ef6868")
                item.result.space_saved_bytes = 0
                item.result.reduction_percent = 0
                window._on_job_updated_ui(item)
                self.assertNotIn(
                    window.queue_table.item(0, 7).foreground().color().name(),
                    ("#31b878", "#ef6868")
                )
                self.assertEqual(window._selected_job_id(), None)
                window.queue_table.selectRow(0)
                self.assertEqual(window._selected_job_id(), "job-color")
            finally:
                window.close()
                app.processEvents()


class QueueRetryTests(unittest.TestCase):
    def test_retry_one_canceled_job_and_no_retry_completed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manager = QueueManager(binaries=object(), work_dir=root / "work")
            source = root / "original.mp4"
            source.write_bytes(b"data")
            item = manager.enqueue(EncodingJobSpec(
                job_id="cancel-retry", input_path=source,
                output_path=root / "output.mp4"
            ), auto_start=False)
            self.assertTrue(manager.cancel_job("cancel-retry"))
            self.assertEqual(item.status, JobStatus.CANCELLED)
            with mock.patch.object(manager, "start") as start:
                self.assertTrue(manager.retry_job("cancel-retry"))
                start.assert_called_once()
            self.assertEqual(item.status, JobStatus.QUEUED)
            self.assertIsNone(item.started_at)
            self.assertIsNone(item.finished_at)
            self.assertFalse(manager.retry_job("cancel-retry"))
            item.status = JobStatus.COMPLETED
            self.assertFalse(manager.retry_job("cancel-retry"))


if __name__ == "__main__":
    unittest.main()
