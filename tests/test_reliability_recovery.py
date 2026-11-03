"""Reliability tests: durable queue, larger-output policy, background import, quality."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from pocketvideo.core.models import (
    EncodingJobSpec, JobStatus, OutputPolicy, OperationMode, PresetId,
    ResourceLimits, ResourceMode, ProcessPriority,
)
from pocketvideo.queue.persistence import QueueJournal
from pocketvideo.media.quality import analyze_video_quality


class QueueJournalTests(unittest.TestCase):
    def test_specs_survive_restart_and_do_not_run_automatically(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "มือถือ.mp4"
            target = root / "outputs" / "มือถือ_small.mp4"
            spec = EncodingJobSpec(
                job_id="recovery-1", input_path=source, output_path=target,
                operation_mode=OperationMode.COMPRESS, preset_id=PresetId.CUSTOM_SIZE,
                target_size_mb=20.0, source_fingerprint=(1, 2, 3, 4),
                output_policy=OutputPolicy.REPLACE_SOURCE_BACKUP,
                skip_larger_outputs=True,
                resource_limits=ResourceLimits(
                    mode=ResourceMode.CUSTOM, cpu_hard_cap_percent=50,
                    process_priority=ProcessPriority.IDLE,
                ),
            )
            path = root / "queue.json"
            journal = QueueJournal(path)
            journal.save([(spec, "processing")])
            restored = journal.load()
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0].input_path, source)
            self.assertEqual(restored[0].output_path, target)
            self.assertEqual(restored[0].source_fingerprint, (1, 2, 3, 4))
            self.assertEqual(restored[0].resource_limits.cpu_hard_cap_percent, 50)
            self.assertEqual(restored[0].resource_limits.mode, ResourceMode.CUSTOM)
            self.assertEqual(restored[0].output_policy, OutputPolicy.REPLACE_SOURCE_BACKUP)
            self.assertTrue(restored[0].skip_larger_outputs)

            class FakeBinaries:
                pass
            from pocketvideo.queue.manager import QueueManager
            manager = QueueManager(
                binaries=FakeBinaries(), work_dir=root / "tmp", journal_path=path
            )
            self.assertEqual(len(manager.items()), 1)
            self.assertEqual(manager.items()[0].status, JobStatus.FAILED)
            self.assertIn("Retry Failed", manager.items()[0].error_message)
            self.assertFalse(manager._scheduler_thread)
            self.assertFalse(source.exists())

    def test_corrupt_or_unknown_json_does_not_execute_anything(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.json"
            path.write_text("not JSON", encoding="utf-8")
            self.assertEqual(QueueJournal(path).load(), [])
            path.write_text(json.dumps(
                {"schema_version": 999, "pending": [{"spec": {"input_path": "X"}}]}
            ), encoding="utf-8")
            self.assertEqual(QueueJournal(path).load(), [])


class LargerOutputPolicyTests(unittest.TestCase):
    def test_very_small_png_preserves_original_if_output_grows(self):
        from PIL import Image
        from pocketvideo.core.config import SettingsManager
        from pocketvideo.ui.controller import AppController
        from pocketvideo.media.binaries import resolve_binaries
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            img = root / "tiny.png"
            Image.new("RGB", (32, 32), (0, 0, 0)).save(img, "PNG", optimize=True)
            original = img.read_bytes()
            try:
                binaries = resolve_binaries()
            except Exception:
                self.skipTest("FFmpeg not installed")
            ctrl = AppController(
                settings_manager=SettingsManager(root / "config"),
                work_dir=root / "work", binaries=binaries,
            )
            added, errors = ctrl.add_files([img])
            self.assertEqual(len(added), 1, errors)
            ctrl.settings.skip_larger_outputs = True
            ctrl.set_output_directory(root / "outputs")
            ctrl.update_output_preferences(image_format="png", suffix="_smaller")
            items = ctrl.enqueue_current_batch(auto_start=True)
            self.assertTrue(ctrl.queue_manager.wait_for_all(timeout=60))
            # Whether Pillow produces a smaller file is codec-dependent; but
            # disabling unsafe overwrite must always preserve the source.
            self.assertEqual(img.read_bytes(), original)
            self.assertIn(items[0].status, (JobStatus.COMPLETED, JobStatus.CANCELLED))
            if items[0].status == JobStatus.CANCELLED:
                self.assertFalse((root / "outputs" / "tiny_smaller.png").exists())


class QualityScoreTests(unittest.TestCase):
    def test_ssim_identity_video_is_one(self):
        from pocketvideo.media.binaries import resolve_binaries
        try:
            binaries = resolve_binaries()
        except Exception:
            self.skipTest("FFmpeg not installed")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source.mkv"
            subprocess.run(
                [str(binaries.ffmpeg), "-hide_banner", "-loglevel", "error",
                 "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=15:duration=2",
                 "-c:v", "ffv1", str(path)], check=True, timeout=30,
            )
            score = analyze_video_quality(
                path, path, binaries, max_seconds=2, include_vmaf=False
            )
            self.assertGreaterEqual(score["ssim"], 0.999)


if __name__ == "__main__":
    unittest.main()
