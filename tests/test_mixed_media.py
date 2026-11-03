"""Mixed video/image conversion, offline HEIC, overwrite and drive safety tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from pocketvideo.core.config import SettingsManager
from pocketvideo.core.models import (
    AfterExportAction, ImagePreset, JobStatus, OutputPolicy, OperationMode,
)
from pocketvideo.core.output_safety import (
    StorageDisconnectedError, fingerprint, make_output_path,
    promote_verified_output, validate_suffix,
)
from pocketvideo.media.images import inspect_image
from pocketvideo.media.binaries import resolve_binaries
from pocketvideo.queue.manager import discover_videos_in_folder
from pocketvideo.resources.post_export import run_after_export
from pocketvideo.ui.controller import AppController


class OutputSafetyTests(unittest.TestCase):
    def test_filename_suffix_and_uniqueness(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "ต้นฉบับ.jpg"
            source.write_bytes(b"original file")
            candidate = make_output_path(
                source, root, "jpeg", "_ประหยัดพื้นที่",
                OutputPolicy.KEEP_BOTH,
            )
            self.assertEqual(candidate.name, "ต้นฉบับ_ประหยัดพื้นที่.jpeg")
            candidate.write_bytes(b"first output")
            second = make_output_path(
                source, root, "jpeg", "_ประหยัดพื้นที่",
                OutputPolicy.KEEP_BOTH,
            )
            self.assertNotEqual(candidate, second)
            for bad in ("../unsafe", "a/b", "a:b", "a\\b", "." * 3):
                with self.assertRaises(ValueError):
                    validate_suffix(bad)

    def test_replace_source_keeps_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "original.jpg"
            staged = root / ".tmp_result.jpg"
            source.write_bytes(b"original payload")
            staged.write_bytes(b"new verified payload")
            expected = fingerprint(source)
            result, backup = promote_verified_output(
                staged, root / "unused.jpg", source,
                OutputPolicy.REPLACE_SOURCE_BACKUP, expected,
            )
            self.assertEqual(result, source)
            self.assertIsNotNone(backup)
            self.assertEqual(source.read_bytes(), b"new verified payload")
            self.assertEqual(backup.read_bytes(), b"original payload")

    def test_unsafe_source_change_blocks_replace(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "source.jpg"
            staged = root / "staged.jpg"
            source.write_bytes(b"first")
            fp = fingerprint(source)
            source.write_bytes(b"changed")
            staged.write_bytes(b"new output")
            with self.assertRaises(StorageDisconnectedError):
                promote_verified_output(
                    staged, source, source,
                    OutputPolicy.REPLACE_SOURCE_BACKUP, fp,
                )
            self.assertEqual(source.read_bytes(), b"changed")
            self.assertEqual(staged.read_bytes(), b"new output")

    def test_power_action_requires_explicit_consent(self) -> None:
        run_after_export(AfterExportAction.NONE)
        for action in (AfterExportAction.SHUTDOWN, AfterExportAction.SLEEP):
            with self.assertRaises(PermissionError):
                run_after_export(action, user_confirmed=False)


class MixedMediaIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory(prefix="pocketvideo_mixed_")
        self.root = Path(self.temp.name)
        self.img = self.root / "กล้อง Android 001.jpg"
        Image.new("RGB", (320, 240), (21, 100, 186)).save(self.img, "JPEG", quality=95)
        try:
            self.binaries = resolve_binaries()
        except Exception:
            self.binaries = None

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _controller(self) -> AppController:
        return AppController(
            settings_manager=SettingsManager(self.root / "settings"),
            binaries=self.binaries,
            work_dir=self.root / "work",
        )

    def test_heic_encode_decode_and_export(self) -> None:
        controller = self._controller()
        added, errors = controller.add_files([self.img])
        self.assertFalse(errors, errors)
        self.assertEqual(added[0].media_kind, "image")
        controller.set_output_directory(self.root / "results")
        controller.update_output_preferences(
            image_format="heic",
            image_preset=ImagePreset.BALANCED,
            suffix="_heic",
        )
        queued = controller.enqueue_current_batch(auto_start=True)
        self.assertEqual(len(queued), 1)
        self.assertTrue(controller.queue_manager.wait_for_all(timeout=90))
        self.assertEqual(queued[0].status, JobStatus.COMPLETED, queued[0].error_message)
        output = queued[0].result.output_path
        self.assertEqual(output.suffix, ".heic")
        self.assertEqual(inspect_image(output).image_width, 320)
        self.assertEqual(self.img.suffix, ".jpg")
        self.assertTrue(self.img.is_file())

    @unittest.skipUnless(os.environ.get("CI") or os.name == "nt", "FFmpeg fixture integration on CI/Windows")
    def test_import_mixed_folder_and_shared_queue(self) -> None:
        if self.binaries is None:
            self.skipTest("ffmpeg not installed")
        from PIL import Image
        folder = self.root / "mixed"
        folder.mkdir()
        photo = folder / "photo.png"
        Image.new("RGBA", (128, 128), (10, 20, 40, 160)).save(photo)
        video = folder / "phone.mp4"
        subprocess.run(
            [
                str(self.binaries.ffmpeg), "-hide_banner", "-loglevel", "error",
                "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=1",
                "-c:v", "libx264", "-preset", "ultrafast", str(video),
            ],
            check=True, timeout=45,
        )
        self.assertEqual(len(discover_videos_in_folder(folder)), 2)
        c = self._controller()
        added, errors = c.add_folder(folder)
        self.assertFalse(errors, errors)
        self.assertEqual({m.media_kind for m in added}, {"image", "video"})
        c.set_output_directory(self.root / "converted")
        c.update_output_preferences(image_format="webp", suffix="_done")
        items = c.enqueue_current_batch(auto_start=True)
        self.assertTrue(c.queue_manager.wait_for_all(timeout=120))
        self.assertEqual([it.status for it in items], [JobStatus.COMPLETED] * 2,
                         [it.error_message for it in items])
        formats = {it.spec.media_kind: it.result.output_path.suffix for it in items}
        self.assertEqual(formats["image"], ".webp")
        self.assertEqual(formats["video"], ".mp4")
        self.assertTrue(photo.is_file())
        self.assertTrue(video.is_file())

    def test_source_replace_policy_keeps_real_jpeg_backup(self) -> None:
        c = self._controller()
        c.add_files([self.img])
        c.set_output_directory(self.root / "results")
        original = self.img.read_bytes()
        c.update_output_preferences(
            image_format="jpeg", suffix="_ignored",
            policy=OutputPolicy.REPLACE_SOURCE_BACKUP,
        )
        items = c.enqueue_current_batch(auto_start=True)
        self.assertTrue(c.queue_manager.wait_for_all(timeout=90))
        self.assertEqual(items[0].status, JobStatus.COMPLETED, items[0].error_message)
        self.assertEqual(items[0].result.output_path, self.img.resolve())
        backups = list(self.root.glob("*.pocketvideo_backup_*.jpg"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertTrue(self.img.is_file())

    def test_persistent_profile_and_collision_preflight(self) -> None:
        c = self._controller()
        c.add_files([self.img])
        c.update_output_preferences(
            suffix="_android", image_format="webp",
            image_preset=ImagePreset.SMALLEST,
            policy=OutputPolicy.SKIP_EXISTING,
        )
        c2 = self._controller()
        self.assertEqual(c2.filename_suffix, "_android")
        self.assertEqual(c2.image_output_format, "webp")
        self.assertEqual(c2.image_preset, ImagePreset.SMALLEST)
        self.assertEqual(c2.output_policy, OutputPolicy.SKIP_EXISTING)

    def test_missing_media_never_starts_a_job(self) -> None:
        c = self._controller()
        c.add_files([self.img])
        c.set_output_directory(self.root / "results")
        self.img.unlink()  # Simulates dismount after import; app never deletes originals.
        with self.assertRaises(StorageDisconnectedError):
            c.enqueue_current_batch(auto_start=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
