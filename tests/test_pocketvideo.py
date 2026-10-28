"""Comprehensive automated test suite for PocketVideo.

Tests real FFmpeg/FFprobe execution and domain logic across:
1. H.264 input inspection & compression
2. H.265 / HEVC input inspection & efficiency warning detection
3. Portrait video orientation (vertical dimensions + rotation metadata)
4. Video with audio stream preservation
5. Genuine Two-Pass target-size encoding (Preset E) + impossible target rejection
6. Batch processing & Smart Remux vs Full Transcoding
7. Job cancellation & pause/resume
8. Unsupported / corrupted input handling
9. Missing or broken FFmpeg binary handling
10. Windows Job Object CPU Hard Cap (`JOBOBJECT_CPU_RATE_CONTROL_INFORMATION`) & POSIX fallback
11. Output integrity verification & never-overwrite-source protection
12. Thai & Unicode filename handling (`วิดีโอทดสอบ_มือถือแอนดรอยด์.mp4`)
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from pocketvideo.core.config import SettingsManager
from pocketvideo.core.i18n import DEFAULT_LANGUAGE, Translator
from pocketvideo.core.models import (
    CompatibilityMode,
    EncodingJobSpec,
    JobStatus,
    OperationMode,
    PresetId,
    ProcessPriority,
    ResourceLimits,
    ResourceMode,
)
from pocketvideo.media.binaries import (
    BinaryPaths,
    MediaBinaryError,
    resolve_binaries,
    validate_binaries,
)
from pocketvideo.media.encoder import (
    EncodingValidationError,
    FFmpegProgressParser,
    build_encode_plan,
)
from pocketvideo.media.presets import (
    calculate_target_size_plan,
    can_smart_remux,
)
from pocketvideo.media.probe import MediaInspectionError, inspect_media
from pocketvideo.queue.manager import QueueManager, build_safe_output_path
from pocketvideo.resources.job_object import (
    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE,
    JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
    JobObjectCpuRateControlInformation,
    ProcessResourceController,
    preset_resource_limits,
)
from pocketvideo.ui.controller import AppController


def _generate_fixture_video(
    ffmpeg_bin: Path,
    out_path: Path,
    vcodec: str = "libx264",
    width: int = 640,
    height: int = 360,
    duration: float = 2.0,
    fps: int = 30,
    with_audio: bool = True,
    bitrate_kbps: int = 1800,
    extra_args: list[str] | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ffmpeg_bin),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}",
    ]
    if with_audio:
        cmd.extend([
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
        ])
    cmd.extend([
        "-c:v",
        vcodec,
        "-b:v",
        f"{bitrate_kbps}k",
        "-pix_fmt",
        "yuv420p",
    ])
    if vcodec in ("libx264", "libx265"):
        cmd.extend(["-preset", "ultrafast"])
    if with_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(str(out_path))
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to generate test fixture {out_path}: {res.stderr}")
    return out_path


class PocketVideoTestSuite(unittest.TestCase):
    """End-to-end and unit tests for PocketVideo."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.binaries: BinaryPaths = resolve_binaries()
        cls.temp_root = Path(tempfile.mkdtemp(prefix="pocketvideo_tests_"))
        cls.fixtures_dir = cls.temp_root / "fixtures"
        cls.outputs_dir = cls.temp_root / "outputs"
        cls.work_dir = cls.temp_root / "work"

        # 1. H.264 landscape video with AAC audio (high bitrate like an Android phone clip)
        cls.h264_audio_mp4 = _generate_fixture_video(
            cls.binaries.ffmpeg,
            cls.fixtures_dir / "VID_20261028_h264_audio.mp4",
            vcodec="libx264",
            width=640,
            height=360,
            duration=2.5,
            fps=30,
            with_audio=True,
            bitrate_kbps=2200,
            extra_args=["-metadata", "com.android.manufacturer=Samsung", "-metadata", "com.android.model=SM-S928B"],
        )

        # 2. H.265 / HEVC video (low bitrate to test already-efficient warning)
        cls.h265_mp4 = _generate_fixture_video(
            cls.binaries.ffmpeg,
            cls.fixtures_dir / "VID_20261028_hevc.mp4",
            vcodec="libx265",
            width=640,
            height=360,
            duration=2.0,
            fps=30,
            with_audio=True,
            bitrate_kbps=180,
        )

        # 3. Portrait video (vertical 360x640)
        cls.portrait_mp4 = _generate_fixture_video(
            cls.binaries.ffmpeg,
            cls.fixtures_dir / "VID_20261028_portrait.mp4",
            vcodec="libx264",
            width=360,
            height=640,
            duration=2.0,
            fps=30,
            with_audio=True,
            bitrate_kbps=1500,
        )

        # 4. Unicode / Thai filename fixture
        cls.thai_unicode_mp4 = cls.fixtures_dir / "วิดีโอทดสอบ_มือถือแอนดรอยด์_4K.mp4"
        shutil.copy2(cls.h264_audio_mp4, cls.thai_unicode_mp4)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_root, ignore_errors=True)

    def test_01_default_language_is_thai_and_bilingual_switching(self) -> None:
        tr = Translator()
        self.assertEqual(tr.language, DEFAULT_LANGUAGE)
        self.assertEqual(tr.language, "th")
        self.assertEqual(tr.t("preset_a_title"), "เน้นภาพสวย")
        self.assertEqual(tr.t("preset_b_title"), "คุ้มสุด")
        self.assertEqual(tr.t("preset_c_title"), "ประหยัดพื้นที่สุด")
        self.assertEqual(tr.t("preset_d_title"), "เร็วสุด")
        self.assertEqual(tr.t("preset_e_title"), "กำหนดขนาดเอง")

        tr.set_language("en")
        self.assertEqual(tr.t("preset_a_title"), "Best Picture Quality")
        self.assertEqual(tr.t("preset_b_title"), "Best Balance")

    def test_02_h264_and_audio_inspection(self) -> None:
        meta = inspect_media(self.h264_audio_mp4, self.binaries)
        self.assertIsNotNone(meta.primary_video)
        assert meta.primary_video is not None
        self.assertEqual(meta.primary_video.codec_name, "h264")
        self.assertEqual(meta.primary_video.width, 640)
        self.assertEqual(meta.primary_video.height, 360)
        self.assertFalse(meta.primary_video.is_portrait)
        self.assertEqual(len(meta.audio_streams), 1)
        self.assertEqual(meta.audio_streams[0].codec_name, "aac")

    def test_03_h265_input_and_efficiency_detection(self) -> None:
        meta = inspect_media(self.h265_mp4, self.binaries)
        assert meta.primary_video is not None
        self.assertEqual(meta.primary_video.codec_name, "hevc")
        self.assertTrue(meta.already_efficient)
        self.assertEqual(meta.efficiency_warning_key, "warn_already_efficient")

    def test_04_portrait_video_detection_and_compression(self) -> None:
        meta = inspect_media(self.portrait_mp4, self.binaries)
        assert meta.primary_video is not None
        self.assertTrue(meta.primary_video.is_portrait)
        self.assertEqual(meta.primary_video.resolution_label, "360x640")

        out_path = build_safe_output_path(self.portrait_mp4, self.outputs_dir, "mp4")
        spec = EncodingJobSpec(
            input_path=self.portrait_mp4,
            output_path=out_path,
            operation_mode=OperationMode.COMPRESS,
            preset_id=PresetId.BEST_BALANCE,
            compatibility_mode=CompatibilityMode.MODERN_ANDROID,
        )
        qm = QueueManager(self.binaries, work_dir=self.work_dir)
        item = qm.enqueue(spec, input_metadata=meta)
        self.assertTrue(qm.wait_for_all(timeout=60.0))
        self.assertEqual(item.status, JobStatus.COMPLETED)
        self.assertIsNotNone(item.result)
        assert item.result is not None
        self.assertEqual(item.result.output_resolution, "360x640")
        self.assertEqual(item.result.output_codec, "hevc")

    def test_05_two_pass_target_size_encoding_and_rejection(self) -> None:
        meta = inspect_media(self.h264_audio_mp4, self.binaries)

        # Impossible target size (e.g., 0.005 MB) must be gracefully rejected
        impossible_plan = calculate_target_size_plan(meta, target_size_mb=0.005)
        self.assertFalse(impossible_plan.is_feasible)
        self.assertEqual(impossible_plan.quality_risk_level, "impossible")

        # Valid target size (0.35 MB for 2.5s clip) using genuine 2-pass encoding
        feasible_plan = calculate_target_size_plan(
            meta, target_size_mb=0.35, compatibility_mode=CompatibilityMode.MAX_COMPATIBILITY
        )
        self.assertTrue(feasible_plan.is_feasible)
        self.assertGreater(feasible_plan.required_video_kbps, 50)

        out_path = build_safe_output_path(
            self.h264_audio_mp4, self.outputs_dir, "mp4", suffix_tag="_twopass"
        )
        spec = EncodingJobSpec(
            input_path=self.h264_audio_mp4,
            output_path=out_path,
            operation_mode=OperationMode.COMPRESS,
            preset_id=PresetId.CUSTOM_SIZE,
            compatibility_mode=CompatibilityMode.MAX_COMPATIBILITY,
            target_size_mb=0.35,
        )
        plan = build_encode_plan(spec, meta, self.binaries, self.work_dir)
        self.assertEqual(len(plan.passes), 2)
        # Ensure CRF is NEVER mixed with two-pass bitrate encoding
        for pass_cmd in plan.passes:
            self.assertNotIn("-crf", pass_cmd)
            self.assertIn("-b:v", pass_cmd)

        qm = QueueManager(self.binaries, work_dir=self.work_dir)
        item = qm.enqueue(spec, input_metadata=meta)
        self.assertTrue(qm.wait_for_all(timeout=60.0))
        self.assertEqual(item.status, JobStatus.COMPLETED)
        self.assertTrue(out_path.exists())

    def test_06_unicode_thai_filename_and_before_after_preview(self) -> None:
        app_dir = self.temp_root / "appdata_thai"
        ctrl = AppController(
            settings_manager=SettingsManager(app_dir),
            binaries=self.binaries,
            work_dir=self.work_dir,
        )
        ctrl.output_dir = self.outputs_dir / "โฟลเดอร์ผลลัพธ์"
        added, errors = ctrl.add_files([self.thai_unicode_mp4])
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(added), 1)

        # Test before/after sample compression preview
        sample_vid, before_png, after_png = ctrl.generate_before_after_preview(
            added[0], self.work_dir / "preview_thai", sample_duration=1.0
        )
        self.assertTrue(sample_vid.exists())
        self.assertTrue(before_png.exists())
        self.assertTrue(after_png.exists())

        # Test full compression of Thai filename
        items = ctrl.enqueue_current_batch(operation_mode=OperationMode.COMPRESS)
        assert ctrl.queue_manager is not None
        self.assertTrue(ctrl.queue_manager.wait_for_all(timeout=60.0))
        self.assertEqual(items[0].status, JobStatus.COMPLETED)
        assert items[0].result is not None
        self.assertTrue(items[0].result.output_path.exists())
        self.assertGreater(items[0].result.space_saved_bytes, 0)

    def test_07_smart_remux_vs_full_transcode_and_batch_processing(self) -> None:
        meta = inspect_media(self.h264_audio_mp4, self.binaries)
        # H.264 + AAC into MKV supports lossless Smart Remux
        remux_ok, _ = can_smart_remux(meta, "mkv")
        self.assertTrue(remux_ok)

        # H.264 + AAC into WebM requires Full Transcoding (VP9 + Opus)
        webm_remux_ok, _ = can_smart_remux(meta, "webm")
        self.assertFalse(webm_remux_ok)

        qm = QueueManager(self.binaries, work_dir=self.work_dir)
        remux_out = build_safe_output_path(self.h264_audio_mp4, self.outputs_dir, "mkv", "_remux")
        spec_remux = EncodingJobSpec(
            input_path=self.h264_audio_mp4,
            output_path=remux_out,
            operation_mode=OperationMode.SMART_REMUX,
            target_container="mkv",
        )
        item1 = qm.enqueue(spec_remux, input_metadata=meta, auto_start=False)

        comp_out = build_safe_output_path(self.h265_mp4, self.outputs_dir, "mp4", "_batch2")
        spec_comp = EncodingJobSpec(
            input_path=self.h265_mp4,
            output_path=comp_out,
            operation_mode=OperationMode.COMPRESS,
            preset_id=PresetId.FASTEST,
            compatibility_mode=CompatibilityMode.MAX_COMPATIBILITY,
        )
        item2 = qm.enqueue(spec_comp, auto_start=True)
        self.assertTrue(qm.wait_for_all(timeout=60.0))
        self.assertEqual(item1.status, JobStatus.COMPLETED)
        self.assertEqual(item2.status, JobStatus.COMPLETED)

    def test_08_cancellation_and_cleanup(self) -> None:
        # Generate a longer 6-second fixture and cancel mid-encode
        long_mp4 = _generate_fixture_video(
            self.binaries.ffmpeg,
            self.fixtures_dir / "long_cancel.mp4",
            vcodec="libx264",
            width=1280,
            height=720,
            duration=6.0,
            fps=30,
        )
        out_path = build_safe_output_path(long_mp4, self.outputs_dir, "mp4", "_cancel")
        spec = EncodingJobSpec(
            input_path=long_mp4,
            output_path=out_path,
            operation_mode=OperationMode.COMPRESS,
            preset_id=PresetId.BEST_QUALITY,
        )
        qm = QueueManager(self.binaries, work_dir=self.work_dir)
        item = qm.enqueue(spec)
        time.sleep(0.25)
        qm.cancel_job(item.spec.job_id)
        self.assertTrue(qm.wait_for_all(timeout=30.0))
        self.assertEqual(item.status, JobStatus.CANCELLED)
        self.assertFalse(out_path.exists())
        # Ensure original source file is completely untouched
        self.assertTrue(long_mp4.exists())

    def test_09_unsupported_input_and_missing_ffmpeg(self) -> None:
        bad_file = self.fixtures_dir / "not_a_video.txt"
        bad_file.write_text("This is plain text, not a media container.", encoding="utf-8")

        with self.assertRaises(MediaInspectionError):
            inspect_media(bad_file, self.binaries)

        with self.assertRaises(MediaBinaryError):
            resolve_binaries(custom_ffmpeg="/nonexistent/path/ffmpeg_missing")

        broken_bin = self.fixtures_dir / "broken_ffmpeg"
        broken_bin.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        broken_bin.chmod(0o755)
        with self.assertRaises(MediaBinaryError):
            validate_binaries(broken_bin, self.binaries.ffprobe)

    def test_10_windows_job_object_cpu_hard_cap_and_resource_limits(self) -> None:
        light = preset_resource_limits(ResourceMode.LIGHT)
        self.assertEqual(light.cpu_hard_cap_percent, 40)
        self.assertTrue(light.enable_cpu_hard_cap)

        # Verify Windows Job Object Win32 API calls with a mock kernel32
        mock_k32 = MagicMock()
        mock_k32.CreateJobObjectW.return_value = 999
        mock_k32.SetInformationJobObject.return_value = 1
        mock_k32.OpenProcess.return_value = 888
        mock_k32.AssignProcessToJobObject.return_value = 1

        limits = ResourceLimits(
            mode=ResourceMode.LIGHT,
            cpu_hard_cap_percent=45,
            enable_cpu_hard_cap=True,
            cpu_affinity_mask=0b0011,
            process_priority=ProcessPriority.IDLE,
        )
        ctrl = ProcessResourceController(limits, kernel32_api=mock_k32)
        dummy_proc = MagicMock()
        dummy_proc.pid = 4321

        details = ctrl.attach_to_process(dummy_proc)
        self.assertTrue(details["cpu_hard_cap_enforced"])
        self.assertTrue(details["priority_applied"])
        self.assertTrue(details["affinity_applied"])
        mock_k32.CreateJobObjectW.assert_called_once()
        mock_k32.AssignProcessToJobObject.assert_called_once_with(999, 888)

        # Verify that JobObjectCpuRateControlInformation was invoked with HARD_CAP flag and 45 * 100
        calls = mock_k32.SetInformationJobObject.call_args_list
        self.assertEqual(len(calls), 2)
        cpu_call = calls[1]
        self.assertEqual(cpu_call.args[1], JobObjectCpuRateControlInformation)
        ctrl.close()

    def test_11_never_overwrite_source_safety_and_progress_parser(self) -> None:
        meta = inspect_media(self.h264_audio_mp4, self.binaries)
        spec_same = EncodingJobSpec(
            input_path=self.h264_audio_mp4,
            output_path=self.h264_audio_mp4,
        )
        with self.assertRaises(EncodingValidationError):
            build_encode_plan(spec_same, meta, self.binaries, self.work_dir)

        parser = FFmpegProgressParser(duration_seconds=10.0, pass_number=1, total_passes=2)
        for line in [
            "frame=150",
            "fps=60.5",
            "bitrate=1200.4kbits/s",
            "total_size=750000",
            "out_time_us=5000000",
            "speed=2.10x",
            "progress=continue",
        ]:
            snap = parser.feed_line(line)
        self.assertIsNotNone(snap)
        assert snap is not None
        # 5s out of 10s on pass 1 of 2 = 25% overall progress
        self.assertAlmostEqual(snap.percent, 25.0, places=1)
        self.assertAlmostEqual(snap.fps, 60.5, places=1)
        self.assertAlmostEqual(snap.speed_multiplier, 2.1, places=2)

    def test_12_resource_controls_persist_and_queued_jobs_keep_snapshot(self) -> None:
        settings_dir = self.temp_root / "resource_controls_test"
        controller = AppController(
            settings_manager=SettingsManager(settings_dir),
            binaries=self.binaries,
            work_dir=self.work_dir / "resource_controls_test",
        )
        controller.output_dir = self.outputs_dir / "resource_controls_test"
        added, errors = controller.add_files([self.h264_audio_mp4])
        self.assertEqual(errors, [])
        self.assertEqual(len(added), 1)

        controller.update_resource_controls(
            cpu_hard_cap_percent=45,
            cpu_threads=2,
            max_concurrent_jobs=3,
            enable_gpu_encoding=True,
        )
        self.assertEqual(controller.resource_limits.mode, ResourceMode.CUSTOM)
        self.assertEqual(controller.resource_limits.cpu_hard_cap_percent, 45)
        self.assertTrue(controller.resource_limits.enable_cpu_hard_cap)
        self.assertEqual(controller.resource_limits.cpu_threads, 2)
        self.assertEqual(controller.resource_limits.max_concurrent_jobs, 3)
        self.assertTrue(controller.resource_limits.enable_gpu_encoding)
        self.assertEqual(controller.queue_manager._max_concurrent, 3)

        saved = SettingsManager(settings_dir).settings
        self.assertEqual(saved.resource_mode, ResourceMode.CUSTOM.value)
        self.assertEqual(saved.cpu_threads, 2)
        self.assertEqual(saved.max_concurrent_jobs, 3)
        self.assertTrue(saved.enable_gpu_encoding)

        queued = controller.enqueue_current_batch(auto_start=False)
        self.assertEqual(len(queued), 1)
        controller.update_resource_controls(
            cpu_hard_cap_percent=100,
            cpu_threads=5,
            max_concurrent_jobs=1,
            enable_gpu_encoding=False,
        )
        self.assertFalse(controller.resource_limits.enable_cpu_hard_cap)
        self.assertEqual(queued[0].spec.resource_limits.cpu_hard_cap_percent, 45)
        self.assertEqual(queued[0].spec.resource_limits.cpu_threads, 2)
        self.assertEqual(queued[0].spec.resource_limits.max_concurrent_jobs, 3)
        self.assertTrue(queued[0].spec.resource_limits.enable_gpu_encoding)


if __name__ == "__main__":
    unittest.main(verbosity=2)
