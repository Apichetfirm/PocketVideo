"""FFmpeg command planner, machine-readable progress parser, frame extractor, and sample previewer."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Callable, Optional

from pocketvideo.core.models import (
    CompatibilityMode,
    EncodingJobSpec,
    JobProgress,
    MediaMetadata,
    OperationMode,
    PresetId,
    ResourceMode,
    TargetSizePlan,
)
from pocketvideo.media.binaries import (
    BinaryPaths,
    _get_CreationFlags,
    detect_working_gpu_encoder,
)
from pocketvideo.media.presets import (
    PRESET_CATALOG,
    calculate_target_size_plan,
    can_smart_remux,
    resolve_subtitle_handling,
)


class EncodingValidationError(ValueError):
    """Raised during pre-flight validation before starting an encode."""


@dataclass
class PlannedEncodeExecution:
    """Resolved execution plan containing 1 or 2 FFmpeg command passes and warnings."""
    job_spec: EncodingJobSpec
    effective_mode: OperationMode
    video_encoder: str
    audio_encoder: str
    passes: list[list[str]]
    temp_output_path: Path
    final_output_path: Path
    passlogfile_prefix: Optional[Path] = None
    target_plan: Optional[TargetSizePlan] = None
    warnings: list[str] = field(default_factory=list)


def validate_preflight(
    spec: EncodingJobSpec,
    media: MediaMetadata,
    binaries: BinaryPaths,
) -> list[str]:
    """Validate input, output path permissions, free disk space, and option conflicts."""
    warnings: list[str] = []
    in_path = spec.input_path.resolve()
    out_path = spec.output_path.resolve()

    if not in_path.exists() or not in_path.is_file():
        raise EncodingValidationError(f"Input file does not exist: {in_path}")

    if in_path == out_path:
        raise EncodingValidationError(
            "Safety policy violation: Output file path must never overwrite the original source file."
        )

    out_dir = out_path.parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EncodingValidationError(
            f"Cannot create output directory '{out_dir}': {exc}"
        ) from exc

    # Verify write permission in output directory
    if not os.access(out_dir, os.W_OK):
        raise EncodingValidationError(
            f"Output directory is not writable (permission denied): {out_dir}"
        )

    # Check available disk space (require at least estimated output size + 50 MB buffer)
    try:
        disk_usage = shutil.disk_usage(out_dir)
        required_bytes = (
            int(spec.target_size_mb * 1024 * 1024)
            if spec.preset_id == PresetId.CUSTOM_SIZE and spec.target_size_mb
            else int(media.file_size_bytes * 0.85)
        ) + (50 * 1024 * 1024)
        if disk_usage.free < required_bytes:
            raise EncodingValidationError(
                f"Insufficient disk space in '{out_dir}': "
                f"{disk_usage.free // (1024 * 1024)} MB free, need at least {required_bytes // (1024 * 1024)} MB."
            )
    except OSError:
        pass

    # Prevent conflicting CRF and Two-Pass Bitrate settings in Advanced overrides
    if spec.advanced.override_enabled:
        if spec.advanced.force_two_pass and spec.advanced.crf is not None and spec.advanced.video_bitrate_kbps is None:
            raise EncodingValidationError(
                "Invalid encoding configuration: Cannot combine CRF mode with Two-Pass encoding without a target bitrate."
            )

    if media.already_efficient and spec.operation_mode == OperationMode.COMPRESS:
        warnings.append(
            "Input video is already efficiently compressed; recompression may yield minimal space savings."
        )

    primary_v = media.primary_video
    if primary_v and primary_v.is_dolby_vision and spec.operation_mode != OperationMode.SMART_REMUX:
        warnings.append(
            "Dolby Vision source detected: Re-encoding cannot guarantee full proprietary Dolby Vision RPU preservation; base HDR10 transfer characteristics will be preserved where supported."
        )
    elif primary_v and primary_v.is_hdr and spec.operation_mode != OperationMode.SMART_REMUX:
        warnings.append(
            f"{primary_v.hdr_format_name} source detected: 10-bit BT.2020/HDR metadata will be preserved."
        )

    return warnings


def _resolve_thread_count(spec: EncodingJobSpec) -> int:
    if spec.resource_limits.cpu_threads > 0:
        return spec.resource_limits.cpu_threads
    total_cpus = os.cpu_count() or 4
    if spec.resource_limits.mode == ResourceMode.LIGHT:
        return max(1, total_cpus // 4)
    if spec.resource_limits.mode == ResourceMode.BALANCED:
        return max(1, total_cpus // 2)
    return 0  # 0 tells FFmpeg to use optimal multi-threading


def build_encode_plan(
    spec: EncodingJobSpec,
    media: MediaMetadata,
    binaries: BinaryPaths,
    work_dir: Path,
) -> PlannedEncodeExecution:
    """Construct safe, validated FFmpeg command argument lists for 1-pass or 2-pass execution."""
    warnings = validate_preflight(spec, media, binaries)
    work_dir.mkdir(parents=True, exist_ok=True)

    container = spec.target_container.lower().lstrip(".")
    final_out = spec.output_path.resolve()
    temp_out = final_out.with_name(f".pocketvideo_tmp_{spec.job_id}_{final_out.name}")

    # 1. Check Smart Remux eligibility when converting formats or explicitly requested
    effective_mode = spec.operation_mode
    if spec.operation_mode in (OperationMode.SMART_REMUX, OperationMode.CONVERT_TRANSCODE):
        remux_ok, remux_reason = can_smart_remux(media, container)
        if spec.operation_mode == OperationMode.SMART_REMUX and not remux_ok:
            raise EncodingValidationError(f"Smart Remux not supported: {remux_reason}")
        if spec.allow_smart_remux and remux_ok:
            effective_mode = OperationMode.SMART_REMUX
        else:
            effective_mode = OperationMode.CONVERT_TRANSCODE

    sub_mode, sub_warnings = resolve_subtitle_handling(media, container)
    warnings.extend(sub_warnings)

    base_input_args = [
        str(binaries.ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(spec.input_path.resolve()),
    ]

    # Map primary video and all audio streams cleanly
    stream_map_args = ["-map", "0:v:0", "-map", "0:a?"]
    if spec.advanced.preserve_subtitles and sub_mode not in ("none", "drop"):
        stream_map_args.extend(["-map", "0:s?"])

    metadata_args: list[str] = []
    if spec.advanced.preserve_metadata:
        metadata_args.extend(["-map_metadata", "0"])
        if container in ("mp4", "mov"):
            metadata_args.extend(["-movflags", "+faststart+use_metadata_tags"])

    # Fast lossless Smart Remux path
    if effective_mode == OperationMode.SMART_REMUX:
        sub_args = ["-c:s", sub_mode] if sub_mode not in ("none", "drop") else ["-sn"]
        cmd = (
            base_input_args
            + stream_map_args
            + ["-c:v", "copy", "-c:a", "copy"]
            + sub_args
            + metadata_args
            + [str(temp_out)]
        )
        return PlannedEncodeExecution(
            job_spec=spec,
            effective_mode=OperationMode.SMART_REMUX,
            video_encoder="copy",
            audio_encoder="copy",
            passes=[cmd],
            temp_output_path=temp_out,
            final_output_path=final_out,
            warnings=warnings,
        )

    # Determine target video codec & software/hardware encoder
    preset_desc = PRESET_CATALOG[spec.preset_id]
    primary_v = media.primary_video
    is_hdr = bool(primary_v and primary_v.is_hdr)

    if container == "webm":
        desired_codec = "vp9"
    elif container == "avi":
        desired_codec = "h264"
    elif spec.compatibility_mode == CompatibilityMode.MAX_COMPATIBILITY or preset_desc.preset_id == PresetId.BEST_QUALITY:
        desired_codec = (
            "hevc"
            if spec.compatibility_mode == CompatibilityMode.MODERN_ANDROID
            else "h264"
        )
    else:
        desired_codec = "hevc"

    if spec.advanced.override_enabled and spec.advanced.video_codec:
        desired_codec = spec.advanced.video_codec.lower()

    # Select specific encoder implementation
    video_encoder = "libx264"
    using_gpu = False
    want_gpu = (
        preset_desc.prefers_gpu or spec.resource_limits.enable_gpu_encoding
    ) and preset_desc.preset_id != PresetId.CUSTOM_SIZE

    if spec.advanced.override_enabled and spec.advanced.encoder_name:
        cand = spec.advanced.encoder_name
        if cand in binaries.available_video_encoders:
            video_encoder = cand
            using_gpu = any(g in cand for g in ("nvenc", "qsv", "amf"))
        else:
            warnings.append(
                f"Requested encoder '{cand}' is unavailable; falling back to software encoder."
            )

    if not (spec.advanced.override_enabled and spec.advanced.encoder_name):
        if want_gpu and container not in ("webm",):
            gpu_enc = detect_working_gpu_encoder(
                binaries,
                target_codec=desired_codec,
                preferred_vendor=spec.resource_limits.preferred_gpu_encoder,
            )
            if gpu_enc:
                video_encoder = gpu_enc
                using_gpu = True
            else:
                warnings.append(
                    "Compatible GPU hardware encoder was not detected; falling back to fast CPU encoding."
                )

        if not using_gpu:
            if desired_codec in ("hevc", "h265") and "libx265" in binaries.available_video_encoders:
                video_encoder = "libx265"
            elif desired_codec == "vp9" and "libvpx-vp9" in binaries.available_video_encoders:
                video_encoder = "libvpx-vp9"
            elif desired_codec == "av1" and "libsvtav1" in binaries.available_video_encoders:
                video_encoder = "libsvtav1"
            else:
                video_encoder = "libx264"

    # Pixel format and HDR 10-bit preservation
    pix_fmt_args: list[str] = []
    hdr_color_args: list[str] = []
    if is_hdr and video_encoder in ("libx265", "hevc_nvenc", "hevc_qsv", "libsvtav1", "libvpx-vp9"):
        pix_fmt_args = ["-pix_fmt", "yuv420p10le"]
        if primary_v and primary_v.color_primaries:
            hdr_color_args.extend(["-color_primaries", primary_v.color_primaries])
        if primary_v and primary_v.color_transfer:
            hdr_color_args.extend(["-color_trc", primary_v.color_transfer])
        if primary_v and primary_v.color_space:
            hdr_color_args.extend(["-colorspace", primary_v.color_space])
    else:
        pix_fmt_args = ["-pix_fmt", "yuv420p"]
        if is_hdr:
            warnings.append(
                f"Encoder '{video_encoder}' does not preserve 10-bit HDR metadata in this profile; outputting 8-bit yuv420p."
            )

    # VFR / Timestamp preservation & optional resolution/FPS filters
    vf_filters: list[str] = []
    if spec.advanced.override_enabled and (spec.advanced.max_width or spec.advanced.max_height):
        mw = spec.advanced.max_width or -2
        mh = spec.advanced.max_height or -2
        vf_filters.append(f"scale='min({mw},iw)':-2")

    filter_args: list[str] = []
    if vf_filters:
        filter_args.extend(["-vf", ",".join(vf_filters)])

    fps_args: list[str] = []
    if spec.advanced.override_enabled and spec.advanced.target_fps:
        fps_args.extend(["-r", str(spec.advanced.target_fps)])
    elif primary_v and primary_v.is_vfr:
        fps_args.extend(["-fps_mode", "vfr"])

    threads = _resolve_thread_count(spec)
    thread_args = ["-threads", str(threads)] if threads > 0 else []

    if not spec.advanced.preserve_subtitles or sub_mode in ("none", "drop"):
        sub_args = ["-sn"]
    else:
        sub_args = ["-c:s", sub_mode]

    # Audio encoding arguments
    audio_encoder = "aac"
    audio_args: list[str] = []
    if not media.audio_streams:
        audio_encoder = "none"
        audio_args = ["-an"]
    elif container == "webm":
        audio_encoder = "libopus" if "libopus" in binaries.available_audio_encoders else "opus"
        audio_args = ["-c:a", audio_encoder, "-b:a", "128k"]
    elif spec.advanced.override_enabled and spec.advanced.audio_codec:
        audio_encoder = spec.advanced.audio_codec
        audio_args = ["-c:a", audio_encoder]
        if audio_encoder != "copy" and spec.advanced.audio_bitrate_kbps:
            audio_args.extend(["-b:a", f"{spec.advanced.audio_bitrate_kbps}k"])
    else:
        can_copy_audio = all(
            a.codec_name in ("aac", "mp3", "opus") for a in media.audio_streams
        ) and container in ("mp4", "mov", "mkv")
        if can_copy_audio and spec.preset_id != PresetId.CUSTOM_SIZE:
            audio_encoder = "copy"
            audio_args = ["-c:a", "copy"]
        else:
            audio_encoder = "aac"
            audio_args = ["-c:a", "aac", "-b:a", "128k"]

    speed_preset = (
        spec.advanced.encoder_speed_preset
        if (spec.advanced.override_enabled and spec.advanced.encoder_speed_preset)
        else preset_desc.encoder_preset
    )

    use_two_pass = (
        spec.preset_id == PresetId.CUSTOM_SIZE
        or (
            spec.advanced.override_enabled
            and spec.advanced.force_two_pass
            and spec.advanced.video_bitrate_kbps is not None
        )
    )

    if use_two_pass:
        if using_gpu:
            video_encoder = "libx265" if desired_codec in ("hevc", "h265") else "libx264"
            using_gpu = False
            warnings.append("Switched to software encoder for genuine two-pass bitrate accuracy.")

        target_plan: Optional[TargetSizePlan] = None
        if spec.preset_id == PresetId.CUSTOM_SIZE:
            if not spec.target_size_mb or spec.target_size_mb <= 0:
                raise EncodingValidationError("Custom Target Size preset requires a positive target size in MB.")
            target_plan = calculate_target_size_plan(
                media, spec.target_size_mb, spec.compatibility_mode
            )
            if not target_plan.is_feasible:
                raise EncodingValidationError(target_plan.explanation)
            video_kbps = target_plan.required_video_kbps
            if target_plan.warning_message_key:
                warnings.append(target_plan.explanation)
            if media.audio_streams:
                if target_plan.audio_codec_action == "copy" and container in ("mp4", "mov", "mkv"):
                    audio_encoder = "copy"
                    audio_args = ["-c:a", "copy"]
                else:
                    per_stream_kbps = max(
                        64, (target_plan.audio_total_bps // 1000) // max(1, len(media.audio_streams))
                    )
                    audio_encoder = "aac" if container != "webm" else "libopus"
                    audio_args = ["-c:a", audio_encoder, "-b:a", f"{per_stream_kbps}k"]
        else:
            video_kbps = int(spec.advanced.video_bitrate_kbps or 1500)

        passlog_prefix = work_dir / f"twopass_{spec.job_id}"
        null_sink = "NUL" if os.name == "nt" else "/dev/null"

        common_v_args = [
            "-c:v",
            video_encoder,
            "-b:v",
            f"{video_kbps}k",
        ]
        if video_encoder in ("libx264", "libx265"):
            common_v_args.extend(["-preset", speed_preset])
            if video_encoder == "libx265" and container in ("mp4", "mov"):
                common_v_args.extend(["-tag:v", "hvc1"])

        if video_encoder == "libx265":
            stats_file = str(passlog_prefix.with_suffix(".x265.log")).replace("\\", "/")
            pass1_v = common_v_args + ["-x265-params", f"pass=1:no-slow-firstpass=1:stats={stats_file}"]
            pass2_v = common_v_args + ["-x265-params", f"pass=2:stats={stats_file}"]
        else:
            pass1_v = common_v_args + ["-pass", "1", "-passlogfile", str(passlog_prefix)]
            pass2_v = common_v_args + ["-pass", "2", "-passlogfile", str(passlog_prefix)]

        pass1_cmd = (
            base_input_args
            + ["-map", "0:v:0"]
            + thread_args
            + filter_args
            + fps_args
            + pix_fmt_args
            + hdr_color_args
            + pass1_v
            + ["-an", "-sn", "-f", "null", null_sink]
        )
        pass2_cmd = (
            base_input_args
            + stream_map_args
            + thread_args
            + filter_args
            + fps_args
            + pix_fmt_args
            + hdr_color_args
            + pass2_v
            + audio_args
            + sub_args
            + metadata_args
            + [str(temp_out)]
        )
        return PlannedEncodeExecution(
            job_spec=spec,
            effective_mode=effective_mode,
            video_encoder=video_encoder,
            audio_encoder=audio_encoder,
            passes=[pass1_cmd, pass2_cmd],
            temp_output_path=temp_out,
            final_output_path=final_out,
            passlogfile_prefix=passlog_prefix,
            target_plan=target_plan,
            warnings=warnings,
        )

    v_encode_args = ["-c:v", video_encoder]
    if spec.advanced.override_enabled and spec.advanced.video_bitrate_kbps is not None:
        v_encode_args.extend(["-b:v", f"{spec.advanced.video_bitrate_kbps}k"])
    else:
        crf_val = (
            spec.advanced.crf
            if (spec.advanced.override_enabled and spec.advanced.crf is not None)
            else (
                preset_desc.default_crf_h265
                if "265" in video_encoder or "hevc" in video_encoder or "vp9" in video_encoder or "av1" in video_encoder
                else preset_desc.default_crf_h264
            )
        )
        if using_gpu:
            if "nvenc" in video_encoder:
                v_encode_args.extend(["-rc", "vbr", "-cq", str(crf_val), "-preset", "p4"])
            elif "qsv" in video_encoder:
                v_encode_args.extend(["-global_quality", str(crf_val)])
            elif "amf" in video_encoder:
                v_encode_args.extend(["-rc", "cqp", "-qp_i", str(crf_val), "-qp_p", str(crf_val)])
        elif video_encoder == "libvpx-vp9":
            v_encode_args.extend(["-crf", str(crf_val), "-b:v", "0", "-cpu-used", "2"])
        elif video_encoder == "libsvtav1":
            v_encode_args.extend(["-crf", str(crf_val), "-preset", "6"])
        else:
            v_encode_args.extend(["-crf", str(crf_val), "-preset", speed_preset])
            if video_encoder == "libx265" and container in ("mp4", "mov"):
                v_encode_args.extend(["-tag:v", "hvc1"])

    single_cmd = (
        base_input_args
        + stream_map_args
        + thread_args
        + filter_args
        + fps_args
        + pix_fmt_args
        + hdr_color_args
        + v_encode_args
        + audio_args
        + sub_args
        + metadata_args
        + [str(temp_out)]
    )

    return PlannedEncodeExecution(
        job_spec=spec,
        effective_mode=effective_mode,
        video_encoder=video_encoder,
        audio_encoder=audio_encoder,
        passes=[single_cmd],
        temp_output_path=temp_out,
        final_output_path=final_out,
        warnings=warnings,
    )


class FFmpegProgressParser:
    """Stateful parser for FFmpeg's machine-readable `-progress pipe:1` key=value stream."""

    def __init__(
        self,
        duration_seconds: float,
        pass_number: int = 1,
        total_passes: int = 1,
        start_time: Optional[float] = None,
    ) -> None:
        self.duration_seconds = max(0.001, duration_seconds)
        self.pass_number = pass_number
        self.total_passes = max(1, total_passes)
        self.start_time = start_time if start_time is not None else time.monotonic()
        self._current_block: dict[str, str] = {}

    def feed_line(self, line: str) -> Optional[JobProgress]:
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            return None
        key, val = stripped.split("=", 1)
        key = key.strip()
        val = val.strip()
        self._current_block[key] = val

        if key == "progress":
            snapshot = self._build_progress(is_end=(val == "end"))
            self._current_block.clear()
            return snapshot
        return None

    def _build_progress(self, is_end: bool = False) -> JobProgress:
        out_us_str = self._current_block.get("out_time_us") or self._current_block.get("out_time_ms") or "0"
        try:
            out_us = int(out_us_str)
        except ValueError:
            out_us = 0
        out_sec = max(0.0, out_us / 1_000_000.0)

        fps_str = self._current_block.get("fps", "0")
        try:
            fps = float(fps_str)
        except ValueError:
            fps = 0.0

        speed_raw = self._current_block.get("speed", "0x").rstrip("x").strip()
        try:
            speed = float(speed_raw) if speed_raw != "N/A" else 0.0
        except ValueError:
            speed = 0.0

        bitrate_raw = self._current_block.get("bitrate", "0").replace("kbits/s", "").strip()
        try:
            bitrate_kbps = float(bitrate_raw) if bitrate_raw != "N/A" else 0.0
        except ValueError:
            bitrate_kbps = 0.0

        size_raw = self._current_block.get("total_size", "0")
        try:
            total_size = int(size_raw) if size_raw.isdigit() else 0
        except ValueError:
            total_size = 0

        pass_fraction = min(1.0, out_sec / self.duration_seconds)
        if is_end:
            pass_fraction = 1.0

        overall_fraction = ((self.pass_number - 1) + pass_fraction) / self.total_passes
        percent = round(min(100.0, max(0.0, overall_fraction * 100.0)), 1)

        elapsed = max(0.001, time.monotonic() - self.start_time)
        eta: Optional[float] = None
        if 0.01 < overall_fraction < 1.0:
            eta = max(0.0, (elapsed / overall_fraction) - elapsed)
        elif is_end:
            eta = 0.0

        return JobProgress(
            pass_number=self.pass_number,
            total_passes=self.total_passes,
            out_time_seconds=round(out_sec, 2),
            duration_seconds=self.duration_seconds,
            percent=percent,
            fps=round(fps, 1),
            speed_multiplier=round(speed, 2),
            bitrate_kbps=round(bitrate_kbps, 1),
            current_size_bytes=total_size,
            elapsed_seconds=round(elapsed, 1),
            eta_seconds=round(eta, 1) if eta is not None else None,
        )


def cleanup_twopass_logs(passlogfile_prefix: Optional[Path]) -> None:
    """Remove temporary two-pass stats files created by libx264/libx265/libvpx."""
    if not passlogfile_prefix:
        return
    parent = passlogfile_prefix.parent
    stem = passlogfile_prefix.name
    if not parent.exists():
        return
    for item in parent.iterdir():
        if item.name.startswith(stem):
            try:
                item.unlink()
            except OSError:
                pass


def extract_representative_frame(
    video_path: Path,
    output_image_path: Path,
    binaries: BinaryPaths,
    timestamp_seconds: Optional[float] = None,
    duration_seconds: float = 0.0,
) -> Path:
    """Extract a PNG frame from a video for visual before/after quality inspection."""
    output_image_path.parent.mkdir(parents=True, exist_ok=True)
    seek_sec = (
        timestamp_seconds
        if timestamp_seconds is not None
        else (duration_seconds * 0.25 if duration_seconds > 0 else 0.5)
    )
    cmd = [
        str(binaries.ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{max(0.0, seek_sec):.3f}",
        "-i",
        str(video_path.resolve()),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_image_path.resolve()),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=_get_CreationFlags(),
    )
    if proc.returncode != 0 or not output_image_path.exists():
        raise RuntimeError(f"Failed to extract video frame: {proc.stderr.strip()}")
    return output_image_path


def generate_sample_compression(
    spec: EncodingJobSpec,
    media: MediaMetadata,
    binaries: BinaryPaths,
    sample_output_path: Path,
    sample_duration_seconds: float = 3.0,
    progress_callback: Optional[Callable[[JobProgress], None]] = None,
) -> tuple[Path, Path, Path]:
    """Generate a short sample encode and extract aligned before/after comparison frames."""
    sample_output_path.parent.mkdir(parents=True, exist_ok=True)
    start_offset = min(max(0.0, media.duration_seconds * 0.2), max(0.0, media.duration_seconds - sample_duration_seconds))
    clip_dur = min(sample_duration_seconds, media.duration_seconds)

    sample_spec = EncodingJobSpec(
        input_path=spec.input_path,
        output_path=sample_output_path,
        operation_mode=OperationMode.COMPRESS,
        preset_id=spec.preset_id if spec.preset_id != PresetId.CUSTOM_SIZE else PresetId.BEST_BALANCE,
        compatibility_mode=spec.compatibility_mode,
        target_container=spec.target_container,
        advanced=spec.advanced,
        resource_limits=spec.resource_limits,
    )
    plan = build_encode_plan(sample_spec, media, binaries, sample_output_path.parent)
    base_cmd = plan.passes[0]
    out_arg = base_cmd[-1]
    trimmed_cmd = base_cmd[:-1] + ["-ss", f"{start_offset:.2f}", "-t", f"{clip_dur:.2f}", out_arg]

    parser = FFmpegProgressParser(duration_seconds=clip_dur, pass_number=1, total_passes=1)
    with subprocess.Popen(
        trimmed_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_get_CreationFlags(),
    ) as proc:
        assert proc.stdout is not None
        assert proc.stderr is not None
        for line in proc.stdout:
            prog = parser.feed_line(line)
            if prog and progress_callback:
                progress_callback(prog)
        err_out = proc.stderr.read()
        proc.wait(timeout=60)
        rc = proc.returncode

    if rc != 0 or not plan.temp_output_path.exists():
        raise RuntimeError(f"Sample compression failed: {err_out.strip()}")

    os.replace(plan.temp_output_path, sample_output_path)

    mid_offset = clip_dur * 0.5
    before_png = sample_output_path.with_name(sample_output_path.stem + "_before.png")
    after_png = sample_output_path.with_name(sample_output_path.stem + "_after.png")

    extract_representative_frame(
        spec.input_path,
        before_png,
        binaries,
        timestamp_seconds=start_offset + mid_offset,
        duration_seconds=media.duration_seconds,
    )
    extract_representative_frame(
        sample_output_path,
        after_png,
        binaries,
        timestamp_seconds=mid_offset,
        duration_seconds=clip_dur,
    )
    return sample_output_path, before_png, after_png
