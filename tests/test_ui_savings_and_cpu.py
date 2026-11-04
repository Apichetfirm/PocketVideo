"""Qt tests: custom CPU limit and historical share card without source files."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE

if PYSIDE6_AVAILABLE:
    from PySide6 import QtGui, QtWidgets
    from pocketvideo.core.config import SettingsManager
    from pocketvideo.core.models import JobResultComparison, OperationMode
    from pocketvideo.ui.controller import AppController
    from pocketvideo.ui.main_window import PocketVideoMainWindow


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 unavailable")
class StatsQtTests(unittest.TestCase):
    def test_custom_cpu_cap_and_saved_value_reloads(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = root / "settings"
            first = AppController(settings_manager=SettingsManager(settings), work_dir=root / "work")
            window = PocketVideoMainWindow(first)
            try:
                presets = [
                    window.cpu_cap_combo.itemData(i)
                    for i in range(window.cpu_cap_combo.count() - 1)
                ]
                self.assertEqual(
                    presets, [10, 20, 30, 40, 50, 60, 70, 75, 80, 85, 90, 95, 100]
                )
                window.cpu_cap_combo.setCurrentIndex(window.cpu_cap_combo.findData(20))
                self.assertEqual(first.resource_limits.cpu_hard_cap_percent, 20)
                idx = window.cpu_cap_combo.findData("custom")
                window.cpu_cap_combo.setCurrentIndex(idx)
                window.cpu_custom_spin.setValue(37)
                self.assertEqual(first.resource_limits.cpu_hard_cap_percent, 37)
                self.assertTrue(window.cpu_custom_spin.isVisible() or not window.isVisible())
            finally:
                window.close()
            second = AppController(
                settings_manager=SettingsManager(settings),
                work_dir=root / "work2",
            )
            self.assertEqual(second.resource_limits.cpu_hard_cap_percent, 37)
            again = PocketVideoMainWindow(second)
            try:
                self.assertEqual(again.cpu_cap_combo.currentData(), "custom")
                self.assertEqual(again.cpu_custom_spin.value(), 37)
                self.assertIn("FFmpeg", again.lbl_threads_help.text())
            finally:
                again.close()
                app.processEvents()

    def test_history_works_after_deleting_both_files_and_share_to_clipboard(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mgr = SettingsManager(root / "settings")
            src, out = root / "original.mp4", root / "small.mp4"
            src.write_bytes(b"a" * 1000)
            out.write_bytes(b"b" * 400)
            result = JobResultComparison(
                job_id="success", input_path=src, output_path=out,
                operation_mode=OperationMode.COMPRESS,
                original_size_bytes=1000, output_size_bytes=400,
                space_saved_bytes=600, reduction_percent=60,
                original_resolution="640x480", output_resolution="640x480",
                original_codec="h264", output_codec="hevc",
                processing_duration_seconds=1,
            )
            mgr.append_history(result)
            src.unlink()
            out.unlink()
            window = PocketVideoMainWindow(
                AppController(settings_manager=SettingsManager(root / "settings"),
                              work_dir=root / "work")
            )
            try:
                window._switch_page(3)
                self.assertEqual(window.history_table.rowCount(), 1)
                self.assertIn("ไม่มี", window.history_table.item(0, 7).text())
                self.assertIn("600", window.history_total_value.text())
                window._copy_history_stats()
                self.assertIn("600", QtGui.QGuiApplication.clipboard().text())
                window._copy_history_image()
                self.assertFalse(QtGui.QGuiApplication.clipboard().pixmap().isNull())
                self.assertEqual(window.history_table.columnCount(), 9)
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
