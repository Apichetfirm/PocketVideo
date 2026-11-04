"""Processing queue per-job destination regression tests.

The displayed location must come from each QueueItem's output specification
and from the verified result when available, never from the most recently
selected global destination folder.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pocketvideo.core.models import (
    JobProgress, JobResultComparison, JobStatus, OperationMode,
)
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE
from pocketvideo.ui.controller import AppController

if PYSIDE6_AVAILABLE:
    from PySide6 import QtWidgets
    from pocketvideo.ui.main_window import PocketVideoMainWindow


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is required")
class ProcessingQueueDestinationTests(unittest.TestCase):
    def test_destination_per_job_and_verified_final_path(self) -> None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory(prefix="pocketvideo_destinations_") as d:
            root = Path(d)
            out_a = root / "DriveA" / "Camera clips"
            out_b = root / "DriveB" / "Android compressed"
            out_a.mkdir(parents=True)
            out_b.mkdir(parents=True)

            source = root / "มือถือ_001.mp4"
            source.write_bytes(b"dummy source")
            final_path = out_b / "มือถือ_001_small.mp4"
            final_path.write_bytes(b"dummy output")

            # Queue item A is pending; its declared output destination is A.
            pending = SimpleNamespace(
                spec=SimpleNamespace(
                    job_id="job-a",
                    input_path=source,
                    output_path=out_a / "มือถือ_001_small.mp4",
                ),
                status=JobStatus.QUEUED,
                progress=JobProgress(),
                result=None,
                error_message="",
                resource_summary="",
                started_at=None, finished_at=None,
            )
            # Queue item B was written to B; the planned staging path is in
            # another folder, so the verified result MUST take precedence.
            result = JobResultComparison(
                job_id="job-b",
                input_path=source,
                output_path=final_path,
                operation_mode=OperationMode.COMPRESS,
                original_size_bytes=150,
                output_size_bytes=80,
                space_saved_bytes=70,
                reduction_percent=46.67,
                original_resolution="1920x1080",
                output_resolution="1920x1080",
                original_codec="h264",
                output_codec="hevc",
                processing_duration_seconds=1.0,
            )
            completed = SimpleNamespace(
                spec=SimpleNamespace(
                    job_id="job-b",
                    input_path=source,
                    output_path=root / "staging" / "intermediate.mp4",
                ),
                status=JobStatus.COMPLETED,
                progress=JobProgress(percent=100.0),
                result=result,
                error_message="",
                resource_summary="",
                started_at="2026-11-04T14:30:00+07:00",
                finished_at="2026-11-04T14:31:00+07:00",
            )

            controller = AppController()
            controller.output_dir = root / "unrelated-global-output"
            window = PocketVideoMainWindow(controller)
            try:
                self.assertEqual(window.queue_table.columnCount(), 11)
                self.assertEqual(
                    window.queue_table.horizontalHeaderItem(2).text(), "บันทึกที่"
                )
                window._on_job_updated_ui(pending)
                window._on_job_updated_ui(completed)
                self.assertEqual(window.queue_table.rowCount(), 2)

                for row, expected in ((0, out_a), (1, out_b)):
                    cell = window.queue_table.item(row, 2)
                    self.assertEqual(cell.text(), str(expected))
                    self.assertEqual(cell.toolTip(), str(expected))
                    self.assertEqual(cell.data(256), str(expected))  # Qt.UserRole
                    self.assertNotIn("unrelated-global-output", cell.text())

                self.assertEqual(window.queue_table.item(0, 3).text(), "QUEUED")
                self.assertEqual(window.queue_table.item(1, 3).text(), "COMPLETED")
                # Shortcuts are available even for pending jobs.
                self.assertIsNotNone(window.queue_table.cellWidget(0, 8))
                self.assertIsNotNone(window.queue_table.cellWidget(1, 8))
                self.assertEqual(window.queue_table.item(0, 9).text(), "—")
                self.assertIn("2026-11-04", window.queue_table.item(1, 9).text())
                self.assertIn("14:31", window.queue_table.item(1, 10).text())
                self.assertTrue(window.queue_table.isColumnHidden(0))
                self.assertFalse(window.queue_table.isColumnHidden(1))

                # Updating a single job never overwrites another row's path.
                controller.output_dir = root / "different-global-setting"
                pending.status = JobStatus.PROCESSING
                window._on_job_updated_ui(pending)
                self.assertEqual(window.queue_table.item(0, 2).text(), str(out_a))
                self.assertEqual(window.queue_table.item(1, 2).text(), str(out_b))
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
