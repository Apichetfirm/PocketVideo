"""Regression tests for Windows Explorer selection and paired video comparison."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pocketvideo.ui.file_actions import reveal_file_in_location
from pocketvideo.ui.theme import build_stylesheet
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE

try:
    from PySide6 import QtWidgets
    from pocketvideo.ui.video_compare import SynchronizedVideoCompareDialog
    HAS_MULTIMEDIA = True
except ImportError:
    HAS_MULTIMEDIA = False


class WindowsFileLocationTests(unittest.TestCase):
    def test_windows_explorer_selects_actual_unicode_file(self):
        with tempfile.TemporaryDirectory() as root:
            f = Path(root) / "วิดีโอ Before-After.mp4"
            f.write_bytes(b"safe")
            with mock.patch("pocketvideo.ui.file_actions.sys.platform", "win32"), mock.patch(
                "pocketvideo.ui.file_actions.subprocess.Popen"
            ) as popen:
                self.assertTrue(reveal_file_in_location(f))
                args, kwargs = popen.call_args
                self.assertEqual(args[0], ["explorer.exe", f"/select,{f.resolve()}"])
                self.assertFalse(kwargs["shell"])
                self.assertIn(f.name, args[0][1])

    def test_missing_file_does_not_open_explorer(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("pocketvideo.ui.file_actions.subprocess.Popen") as popen:
                with self.assertRaises(FileNotFoundError):
                    reveal_file_in_location(Path(root) / "missing.mp4")
                popen.assert_not_called()

    def test_theme_no_longer_paints_all_label_backgrounds(self):
        for mode in ("dark", "light"):
            qss = build_stylesheet(mode)
            self.assertIn("QLabel {", qss)
            self.assertIn("background-color: transparent;", qss)
            self.assertNotIn("QWidget, QMainWindow, QScrollArea", qss)
            self.assertIn("QWidget#DashboardPage", qss)


@unittest.skipUnless(PYSIDE6_AVAILABLE and HAS_MULTIMEDIA, "Qt Multimedia not installed")
class SynchronizedVideoDialogTests(unittest.TestCase):
    def test_two_video_players_share_transport_and_zoom_control(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as root:
            before = Path(root) / "before.mp4"
            after = Path(root) / "after.mp4"
            # The test verifies Qt/transport construction, not video decoding;
            # these files are placeholders and no playback is started.
            before.write_bytes(b"test")
            after.write_bytes(b"test")
            dialog = SynchronizedVideoCompareDialog(
                before, after, binaries=None, before_offset_ms=1200
            )
            try:
                self.assertEqual(len(dialog._players), 2)
                self.assertEqual(dialog.before_offset_ms, 1200)
                self.assertIs(dialog._players[0], dialog.before_player)
                self.assertIs(dialog._players[1], dialog.after_player)
                self.assertIsNotNone(dialog.zoom_button)
                self.assertIn("▶", dialog.play_button.text())
                dialog.seek_to(0)
                dialog.pause()
            finally:
                dialog.close()
                app.processEvents()


@unittest.skipUnless(PYSIDE6_AVAILABLE and HAS_MULTIMEDIA, "Qt Multimedia not installed")
class MainWindowWiringTests(unittest.TestCase):
    def test_completed_queue_opens_real_dual_player_and_selects_file_location(self):
        from pocketvideo.core.models import JobResultComparison, OperationMode
        from pocketvideo.ui.controller import AppController
        from pocketvideo.ui.main_window import PocketVideoMainWindow
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.mp4"
            result_path = Path(root) / "result.mp4"
            backup = Path(root) / "backup.mp4"
            for file in (source, result_path, backup):
                file.write_bytes(b"video test")
            comparison = JobResultComparison(
                job_id="compare-1", input_path=source, output_path=result_path,
                operation_mode=OperationMode.COMPRESS,
                original_size_bytes=123, output_size_bytes=45,
                space_saved_bytes=78, reduction_percent=63.4,
                original_resolution="640x360", output_resolution="640x360",
                original_codec="h264", output_codec="hevc",
                processing_duration_seconds=1.2,
                source_backup_path=backup,
            )
            controller = AppController()
            controller.binaries = object()  # Only truthiness is used by this launch path.
            window = PocketVideoMainWindow(controller)
            try:
                with mock.patch(
                    "pocketvideo.ui.video_compare.SynchronizedVideoCompareDialog"
                ) as player:
                    window._show_completed_compare(comparison)
                    player.assert_called_once()
                    self.assertEqual(player.call_args.args[0], backup)
                    self.assertEqual(player.call_args.args[1], result_path)
                    player.return_value.exec.assert_called_once()
                with mock.patch(
                    "pocketvideo.ui.main_window.reveal_file_in_location"
                ) as explorer:
                    window._open_output_folder(comparison)
                    explorer.assert_called_once_with(result_path)
                self.assertTrue(window.btn_nav_convert.isHidden())
                self.assertEqual(window.file_table.columnWidth(3), 146)
                self.assertTrue(window.export_options.isHidden())
                window.btn_toggle_export_options.setChecked(True)
                self.assertFalse(window.export_options.isHidden())
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
