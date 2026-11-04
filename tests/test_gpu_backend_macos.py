"""Hardware accelerator choices are capability-probed, not assumed."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from pocketvideo.core.config import SettingsManager, get_default_output_dir
from pocketvideo.media.binaries import BinaryPaths, detect_working_gpu_encoder
from pocketvideo.ui.controller import AppController


class GPUBackendTests(unittest.TestCase):
    def test_apple_videotoolbox_probe_on_macos(self) -> None:
        bins = BinaryPaths(
            ffmpeg=Path("/unused/ffmpeg"), ffprobe=Path("/unused/ffprobe"),
            available_video_encoders={"hevc_videotoolbox", "hevc_nvenc"},
        )
        with mock.patch("pocketvideo.media.binaries.sys.platform", "darwin"), mock.patch(
            "pocketvideo.media.binaries.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            self.assertEqual(
                detect_working_gpu_encoder(bins, target_codec="hevc"),
                "hevc_videotoolbox",
            )
            self.assertIn("hevc_videotoolbox", run.call_args.args[0])

    def test_no_reported_accelerator_falls_back_without_fake_gpu(self):
        bins = BinaryPaths(
            ffmpeg=Path("/unused/ffmpeg"), ffprobe=Path("/unused/ffprobe"),
            available_video_encoders={"libx264", "libx265"},
        )
        with mock.patch("pocketvideo.media.binaries.subprocess.run") as runner:
            self.assertIsNone(detect_working_gpu_encoder(bins))
            runner.assert_not_called()

    def test_vendor_preference_is_persisted_and_validated(self):
        with tempfile.TemporaryDirectory() as d:
            settings = SettingsManager(Path(d) / "config")
            c = AppController(settings_manager=settings)
            c.update_resource_controls(
                enable_gpu_encoding=True,
                preferred_gpu_encoder="videotoolbox",
            )
            self.assertEqual(c.resource_limits.preferred_gpu_encoder, "videotoolbox")
            other = AppController(settings_manager=SettingsManager(Path(d) / "config"))
            self.assertEqual(other.resource_limits.preferred_gpu_encoder, "videotoolbox")
            with self.assertRaises(ValueError):
                c.update_resource_controls(preferred_gpu_encoder="unverified")

    def test_macos_default_output_dir(self):
        with mock.patch("pocketvideo.core.config.sys.platform", "darwin"):
            self.assertEqual(
                get_default_output_dir(), Path.home() / "Movies" / "PocketVideo_Output"
            )


if __name__ == "__main__":
    unittest.main()
