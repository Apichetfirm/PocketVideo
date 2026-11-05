"""Regression coverage for the November compression review.

No file is permanently deleted in these tests: the cleanup gate is validated
separately from Qt's OS trash operation.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pocketvideo.core.config import SettingsManager
from pocketvideo.core.file_metadata import copy_source_timestamps
from pocketvideo.core.models import (
    EncodingJobSpec, JobResultComparison, JobStatus, OperationMode, PresetId,
)
from pocketvideo.core.original_cleanup import verified_original_for_trash
from pocketvideo.core.output_safety import fingerprint
from pocketvideo.media.binaries import BinaryPaths
from pocketvideo.queue.manager import QueueManager
from pocketvideo.ui.controller import AppController
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE

if PYSIDE6_AVAILABLE:
    from PySide6 import QtWidgets
    from pocketvideo.ui.main_window import PocketVideoMainWindow


def comparison(source: Path, output: Path, *, job_id: str = "verified") -> JobResultComparison:
    return JobResultComparison(
        job_id=job_id, input_path=source, output_path=output,
        operation_mode=OperationMode.COMPRESS,
        original_size_bytes=1000, output_size_bytes=200, space_saved_bytes=800,
        reduction_percent=80, original_resolution="640x360",
        output_resolution="640x360", original_codec="h264",
        output_codec="h264", processing_duration_seconds=1.0,
    )


class CoreReviewRegressionTests(unittest.TestCase):
    def test_trash_gate_accepts_verified_identity_and_rejects_changed_files(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "src.mp4", Path(folder) / "out.mp4"
            source.write_bytes(b"a" * 1000)
            output.write_bytes(b"b" * 200)
            expected = fingerprint(source)
            result = comparison(source, output)
            self.assertEqual(verified_original_for_trash(result, expected, fingerprint(output)), source)
            with self.assertRaises(ValueError):
                verified_original_for_trash(result, None, fingerprint(output))
            expected_output = fingerprint(output)
            output.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verified_original_for_trash(result, expected, expected_output)
            output.write_bytes(b"b" * 200)
            expected_output = fingerprint(output)
            source.write_bytes(b"c" * 1100)
            with self.assertRaises(OSError):
                verified_original_for_trash(result, expected, expected_output)

    def test_preserve_source_modification_time(self):
        with tempfile.TemporaryDirectory() as folder:
            source, dest = Path(folder) / "in.mp4", Path(folder) / "out.mp4"
            source.write_bytes(b"source")
            dest.write_bytes(b"output")
            old_ns = 1_600_000_000_000_000_000
            os.utime(source, ns=(old_ns, old_ns))
            source_stat = source.stat()
            copy_source_timestamps(source_stat, dest)
            self.assertLess(abs(dest.stat().st_mtime_ns - source_stat.st_mtime_ns), 10_000_000)
            if os.name == "nt":
                self.assertLess(abs(dest.stat().st_ctime - source_stat.st_ctime), 1.0)

    def test_clear_finished_preserves_live_jobs_and_does_not_delete_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "original.mp4"
            source.write_bytes(b"original")
            qm = QueueManager(
                BinaryPaths(root / "ffmpeg", root / "ffprobe"),
                journal_path=root / "queue.json", work_dir=root / "work",
            )
            done = qm.enqueue(EncodingJobSpec(
                job_id="done", input_path=source, output_path=root / "done.mp4",
            ), auto_start=False)
            pending = qm.enqueue(EncodingJobSpec(
                job_id="pending", input_path=source, output_path=root / "pending.mp4",
            ), auto_start=False)
            done.status = JobStatus.COMPLETED
            self.assertEqual(qm.clear_finished(), 1)
            self.assertIsNone(qm.get_item("done"))
            self.assertEqual(qm.items(), [pending])
            self.assertTrue(source.exists())
            self.assertEqual(qm.clear_finished(), 0)


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 not installed")
class UiReviewRegressionTests(unittest.TestCase):
    def test_completed_callback_reaches_durable_history_and_theme_toggle_is_light(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / "src.mp4", root / "out.mp4"
            source.write_bytes(b"a" * 1000)
            output.write_bytes(b"b" * 200)
            ctrl = AppController(
                settings_manager=SettingsManager(root / "settings"),
                binaries=BinaryPaths(root / "ffmpeg", root / "ffprobe"),
                work_dir=root / "work",
            )
            window = PocketVideoMainWindow(ctrl)
            try:
                self.assertIsNotNone(ctrl.queue_manager)
                self.assertEqual(ctrl.queue_manager.on_job_completed,
                                 window._persist_and_relay_completed)
                window._persist_and_relay_completed(comparison(source, output))
                self.assertEqual(ctrl.settings_mgr.load_savings_stats().net_saved_bytes, 800)
                self.assertEqual(len(ctrl.settings_mgr.load_history()), 1)
                self.assertIn("20.0%", window.history_table.item(0, 4).text())
                source.unlink()
                output.unlink()
                # Theme changes must never perform filesystem existence checks
                # across potentially disconnected original file locations.
                with mock.patch.object(ctrl.settings_mgr, "load_history",
                                       side_effect=AssertionError("heavy history scan")):
                    previous = ctrl.settings.theme
                    window._toggle_theme()
                    self.assertNotEqual(ctrl.settings.theme, previous)
                    self.assertEqual(window.history_total_value.text(), "800 B")
            finally:
                window.close()
                app.processEvents()

    def test_dropdown_1_to_8_wheel_guard_max_saving_and_light_highlight(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ctrl = AppController(
                settings_manager=SettingsManager(root / "settings"),
                binaries=BinaryPaths(root / "ffmpeg", root / "ffprobe"),
            )
            window = PocketVideoMainWindow(ctrl)
            try:
                self.assertEqual(
                    [window.concurrent_spin.itemData(i)
                     for i in range(window.concurrent_spin.count())],
                    list(range(1, 9)),
                )
                window.concurrent_spin.setValue(8)
                self.assertEqual(ctrl.resource_limits.max_concurrent_jobs, 8)
                event = mock.Mock()
                before = window.cpu_cap_combo.currentIndex()
                window.cpu_cap_combo.wheelEvent(event)
                event.ignore.assert_called_once()
                self.assertEqual(window.cpu_cap_combo.currentIndex(), before)
                window._on_preset_selected(PresetId.MAX_SPACE_SAVING)
                self.assertFalse(window.chk_max_two_pass.isHidden())
                window.chk_max_two_pass.setChecked(True)
                self.assertTrue(ctrl.advanced_options.max_saving_two_pass)
                window._on_preset_selected(PresetId.BEST_BALANCE)
                self.assertTrue(window.chk_max_two_pass.isHidden())
                window.apply_theme("light")
                self.assertIn("QFrame#PresetCard[selected=", window.styleSheet())
                self.assertIn("selection-background-color: #0284C7", window.styleSheet())
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
