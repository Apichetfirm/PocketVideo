"""FFprobe media inspection with Android metadata, HDR/Dolby Vision, VFR, and efficiency detection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Optional

from pocketvideo.core.models import (
    AudioStreamInfo,
    MediaMetadata,
    SubtitleStreamInfo,
    VideoStreamInfo,
)
from pocketvideo.media.binaries import BinaryPaths, _get_CreationFlags


class MediaInspectionError(RuntimeError):
    """Raised when an input file is missing, unreadable, or not valid media."""


def _parse_rational(value: Optional[str]) -> float:
    if not value or value in ("0/0", "N/A"):
        return 0.0
    if "/" in value:
        num_str, den_str = value.split("/", 1)
        try:
            num = float(num_str)
            den = float(den_str)
            return num / den if den != 0 else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _infer_bit_depth(stream: dict[str, Any]) -> int:
    raw_bits = stream.get("bits_per_raw_sample")
    if raw_bits and str(raw_bits).isdigit():
        val = int(raw_bits)
        if val in (8, 10, 12):
            return val
    pix_fmt = str(stream.get("pix_fmt", "")).lower()
    if "12le" in pix_fmt or "12be" in pix_fmt or "p012" in pix_fmt:
        return 12
    if "10le" in pix_fmt or "10be" in pix_fmt or "p010" in pix_fmt:
        return 10
    return 8


def _extract_rotation(stream: dict[str, Any]) -> int:
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(float(tags["rotate"])) % 360
        except (ValueError, TypeError):
            pass
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(float(side_data["rotation"])) % 360
            except (ValueError, TypeError):
                pass
    return 0


def _detect_hdr(stream: dict[str, Any], bit_depth: int) -> tuple[bool, bool, str]:
    """Return (is_hdr, is_dolby_vision, hdr_format_name)."""
    codec_tag = str(stream.get("codec_tag_string", "")).lower()
    side_data_list = stream.get("side_data_list") or []
    for sd in side_data_list:
        sd_type = str(sd.get("side_data_type", "")).lower()
        if "dovi" in sd_type or "dolby vision" in sd_type:
            return True, True, "Dolby Vision"
    if codec_tag in ("dvhe", "dvh1", "dav1", "dva1"):
        return True, True, "Dolby Vision"

    color_transfer = str(stream.get("color_transfer", "")).lower()
    color_primaries = str(stream.get("color_primaries", "")).lower()
    if color_transfer == "smpte2084":
        return True, False, "HDR10"
    if color_transfer == "arib-std-b67":
        return True, False, "HLG"
    if bit_depth >= 10 and "bt2020" in color_primaries:
        return True, False, "HDR (BT.2020 10-bit)"
    return False, False, "SDR"


def inspect_media(file_path: str | Path, binaries: BinaryPaths) -> MediaMetadata:
    """Inspect a media file using FFprobe with full Unicode path support."""
    target = Path(file_path).resolve()
    if not target.exists():
        raise MediaInspectionError(f"File does not exist: {target}")
    if not target.is_file():
        raise MediaInspectionError(f"Path is not a regular file: {target}")
    file_size = target.stat().st_size
    if file_size == 0:
        raise MediaInspectionError(f"File is empty (0 bytes): {target}")

    cmd = [
        str(binaries.ffprobe),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(target),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=_get_CreationFlags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaInspectionError(f"FFprobe failed to run on '{target.name}': {exc}") from exc

    if proc.returncode != 0 or not proc.stdout.strip():
        raise MediaInspectionError(
            f"Invalid or unsupported media file: '{target.name}' could not be parsed by FFprobe."
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MediaInspectionError(
            f"FFprobe returned malformed JSON for '{target.name}'."
        ) from exc

    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    if not streams:
        raise MediaInspectionError(f"No media streams found in '{target.name}'.")

    duration = float(fmt.get("duration") or 0.0)
    overall_bps = int(fmt.get("bit_rate") or 0)

    video_streams: list[VideoStreamInfo] = []
    audio_streams: list[AudioStreamInfo] = []
    subtitle_streams: list[SubtitleStreamInfo] = []

    for s in streams:
        codec_type = s.get("codec_type")
        idx = int(s.get("index", 0))
        codec_name = str(s.get("codec_name", "unknown")).lower()

        if codec_type == "video":
            # Skip attached cover art images (mjpeg/png with disposition attached_pic)
            disposition = s.get("disposition") or {}
            if disposition.get("attached_pic") == 1:
                continue
            width = int(s.get("width") or 0)
            height = int(s.get("height") or 0)
            if width <= 0 or height <= 0:
                continue
            avg_fr_raw = str(s.get("avg_frame_rate", "0/0"))
            r_fr_raw = str(s.get("r_frame_rate", "0/0"))
            avg_fps = _parse_rational(avg_fr_raw)
            r_fps = _parse_rational(r_fr_raw)
            fps = avg_fps if avg_fps > 0 else (r_fps if r_fps > 0 else 30.0)
            is_vfr = (
                avg_fps > 0
                and r_fps > 0
                and abs(avg_fps - r_fps) > 1.25
            )
            bit_depth = _infer_bit_depth(s)
            rotation = _extract_rotation(s)
            is_hdr, is_dv, hdr_name = _detect_hdr(s, bit_depth)
            s_bps = int(s["bit_rate"]) if s.get("bit_rate") and str(s["bit_rate"]).isdigit() else None
            if duration <= 0 and s.get("duration"):
                try:
                    duration = max(duration, float(s["duration"]))
                except (ValueError, TypeError):
                    pass

            video_streams.append(
                VideoStreamInfo(
                    index=idx,
                    codec_name=codec_name,
                    codec_long_name=str(s.get("codec_long_name", codec_name)),
                    profile=str(s.get("profile", "")),
                    width=width,
                    height=height,
                    fps=round(fps, 3),
                    avg_frame_rate_raw=avg_fr_raw,
                    r_frame_rate_raw=r_fr_raw,
                    is_vfr=is_vfr,
                    bit_rate=s_bps,
                    pix_fmt=str(s.get("pix_fmt", "yuv420p")),
                    bit_depth=bit_depth,
                    rotation=rotation,
                    color_space=s.get("color_space"),
                    color_transfer=s.get("color_transfer"),
                    color_primaries=s.get("color_primaries"),
                    is_hdr=is_hdr,
                    is_dolby_vision=is_dv,
                    hdr_format_name=hdr_name,
                )
            )
        elif codec_type == "audio":
            a_bps = int(s["bit_rate"]) if s.get("bit_rate") and str(s["bit_rate"]).isdigit() else None
            tags = s.get("tags") or {}
            audio_streams.append(
                AudioStreamInfo(
                    index=idx,
                    codec_name=codec_name,
                    channels=int(s.get("channels") or 2),
                    sample_rate=int(s.get("sample_rate") or 48000),
                    bit_rate=a_bps,
                    language=tags.get("language"),
                    title=tags.get("title"),
                )
            )
        elif codec_type == "subtitle":
            tags = s.get("tags") or {}
            subtitle_streams.append(
                SubtitleStreamInfo(
                    index=idx,
                    codec_name=codec_name,
                    language=tags.get("language"),
                    title=tags.get("title"),
                )
            )

    if not video_streams:
        raise MediaInspectionError(f"File '{target.name}' does not contain a valid video stream.")

    if overall_bps <= 0 and duration > 0:
        overall_bps = int((file_size * 8) / duration)

    fmt_tags = {str(k): str(v) for k, v in (fmt.get("tags") or {}).items()}
    creation_time = fmt_tags.get("creation_time")
    android_make = fmt_tags.get("com.android.manufacturer") or fmt_tags.get("make")
    android_model = fmt_tags.get("com.android.model") or fmt_tags.get("model")

    primary_v = video_streams[0]
    audio_bps_sum = sum((a.bit_rate or 128_000) for a in audio_streams)
    est_video_bps = primary_v.bit_rate or max(100_000, overall_bps - audio_bps_sum)
    pixels_per_sec = max(1.0, primary_v.width * primary_v.height * max(1.0, primary_v.fps))
    bpp = est_video_bps / pixels_per_sec

    already_efficient = False
    efficiency_warning_key: Optional[str] = None
    if primary_v.codec_name in ("hevc", "h265", "av1", "vp9") and bpp < 0.055:
        already_efficient = True
        efficiency_warning_key = "warn_already_efficient"
    elif primary_v.codec_name in ("h264", "avc") and bpp < 0.042:
        already_efficient = True
        efficiency_warning_key = "warn_already_efficient"

    hdr_warning_key: Optional[str] = None
    if primary_v.is_dolby_vision:
        hdr_warning_key = "warn_dolby_vision"
    elif primary_v.is_hdr:
        hdr_warning_key = "warn_hdr_detected"

    container_raw = str(fmt.get("format_name", "")).lower()
    ext_container = target.suffix.lstrip(".").lower() or container_raw.split(",")[0]

    return MediaMetadata(
        file_path=target,
        container_format=ext_container,
        duration_seconds=max(0.001, duration),
        file_size_bytes=file_size,
        overall_bit_rate=overall_bps,
        video_streams=video_streams,
        audio_streams=audio_streams,
        subtitle_streams=subtitle_streams,
        creation_time=creation_time,
        android_make=android_make,
        android_model=android_model,
        already_efficient=already_efficient,
        efficiency_warning_key=efficiency_warning_key,
        hdr_warning_key=hdr_warning_key,
        raw_tags=fmt_tags,
    )
