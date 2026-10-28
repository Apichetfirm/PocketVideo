"""Compression presets (A-E), two-pass target-size bitrate planner, and container compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pocketvideo.core.models import (
    CompatibilityMode,
    MediaMetadata,
    OperationMode,
    PresetId,
    TargetSizePlan,
)


@dataclass(frozen=True)
class PresetDescriptor:
    """Metadata describing one of the five main user-facing compression presets."""
    preset_id: PresetId
    thai_name: str
    english_name: str
    title_key: str
    subtitle_key: str
    badge_key: str
    desc_key: str
    uses_crf: bool
    uses_two_pass: bool
    prefers_gpu: bool
    default_crf_h265: int
    default_crf_h264: int
    encoder_preset: str
    warning_key: Optional[str] = None


PRESET_CATALOG: dict[PresetId, PresetDescriptor] = {
    PresetId.BEST_QUALITY: PresetDescriptor(
        preset_id=PresetId.BEST_QUALITY,
        thai_name="เน้นภาพสวย",
        english_name="Best Picture Quality",
        title_key="preset_a_title",
        subtitle_key="preset_a_en",
        badge_key="preset_a_badge",
        desc_key="preset_a_desc",
        uses_crf=True,
        uses_two_pass=False,
        prefers_gpu=False,
        default_crf_h265=20,
        default_crf_h264=18,
        encoder_preset="slow",
        warning_key=None,
    ),
    PresetId.BEST_BALANCE: PresetDescriptor(
        preset_id=PresetId.BEST_BALANCE,
        thai_name="คุ้มสุด",
        english_name="Best Balance",
        title_key="preset_b_title",
        subtitle_key="preset_b_en",
        badge_key="preset_b_badge",
        desc_key="preset_b_desc",
        uses_crf=True,
        uses_two_pass=False,
        prefers_gpu=False,
        default_crf_h265=24,
        default_crf_h264=22,
        encoder_preset="medium",
        warning_key=None,
    ),
    PresetId.MAX_SPACE_SAVING: PresetDescriptor(
        preset_id=PresetId.MAX_SPACE_SAVING,
        thai_name="ประหยัดพื้นที่สุด",
        english_name="Maximum Space Saving",
        title_key="preset_c_title",
        subtitle_key="preset_c_en",
        badge_key="preset_c_badge",
        desc_key="preset_c_desc",
        uses_crf=True,
        uses_two_pass=False,
        prefers_gpu=False,
        default_crf_h265=29,
        default_crf_h264=27,
        encoder_preset="slow",
        warning_key="warn_preset_c_degradation",
    ),
    PresetId.FASTEST: PresetDescriptor(
        preset_id=PresetId.FASTEST,
        thai_name="เร็วสุด",
        english_name="Fastest Compression",
        title_key="preset_d_title",
        subtitle_key="preset_d_en",
        badge_key="preset_d_badge",
        desc_key="preset_d_desc",
        uses_crf=True,
        uses_two_pass=False,
        prefers_gpu=True,
        default_crf_h265=25,
        default_crf_h264=23,
        encoder_preset="veryfast",
        warning_key="warn_preset_d_efficiency",
    ),
    PresetId.CUSTOM_SIZE: PresetDescriptor(
        preset_id=PresetId.CUSTOM_SIZE,
        thai_name="กำหนดขนาดเอง",
        english_name="Custom Target File Size",
        title_key="preset_e_title",
        subtitle_key="preset_e_en",
        badge_key="preset_e_badge",
        desc_key="preset_e_desc",
        uses_crf=False,
        uses_two_pass=True,
        prefers_gpu=False,
        default_crf_h265=24,
        default_crf_h264=22,
        encoder_preset="medium",
        warning_key=None,
    ),
}


CONTAINER_CODEC_RULES: dict[str, dict[str, set[str]]] = {
    "mp4": {
        "video": {"h264", "hevc", "av1", "mpeg4"},
        "audio": {"aac", "mp3", "ac3", "eac3", "opus", "alac"},
        "subtitle": {"mov_text", "tx3g"},
    },
    "mov": {
        "video": {"h264", "hevc", "prores", "mpeg4"},
        "audio": {"aac", "pcm_s16le", "pcm_s24le", "alac", "mp3", "ac3"},
        "subtitle": {"mov_text", "tx3g"},
    },
    "mkv": {
        "video": {"h264", "hevc", "vp9", "vp8", "av1", "mpeg4", "theora"},
        "audio": {"aac", "opus", "vorbis", "mp3", "flac", "ac3", "eac3", "pcm_s16le"},
        "subtitle": {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "hdmv_pgs_subtitle", "dvd_subtitle"},
    },
    "webm": {
        "video": {"vp9", "vp8", "av1"},
        "audio": {"opus", "vorbis"},
        "subtitle": {"webvtt"},
    },
    "avi": {
        "video": {"h264", "mpeg4", "msmpeg4v3", "mjpeg"},
        "audio": {"mp3", "ac3", "pcm_s16le"},
        "subtitle": set(),
    },
}


def can_smart_remux(media: MediaMetadata, target_container: str) -> tuple[bool, str]:
    """Determine if existing video & audio streams can be losslessly remuxed into target_container."""
    container = target_container.lower().lstrip(".")
    rules = CONTAINER_CODEC_RULES.get(container)
    if not rules:
        return False, f"Container '{container}' does not have a verified lossless remux profile."

    for v in media.video_streams:
        if v.codec_name not in rules["video"]:
            return (
                False,
                f"Video codec '{v.codec_name}' is not natively compatible with .{container} for remuxing.",
            )

    for a in media.audio_streams:
        if a.codec_name not in rules["audio"]:
            return (
                False,
                f"Audio codec '{a.codec_name}' is not natively compatible with .{container} for remuxing.",
            )

    return True, f"All video ({media.primary_video.codec_name if media.primary_video else 'none'}) and audio streams are compatible with .{container} for fast lossless remux."


def resolve_subtitle_handling(
    media: MediaMetadata, target_container: str
) -> tuple[str, list[str]]:
    """Return ('copy' | 'mov_text' | 'drop', warnings) for subtitle streams."""
    if not media.subtitle_streams:
        return "none", []
    container = target_container.lower().lstrip(".")
    rules = CONTAINER_CODEC_RULES.get(container, {"subtitle": set()})
    supported_subs = rules.get("subtitle", set())
    warnings: list[str] = []

    if container == "mkv":
        return "copy", []

    if container in ("mp4", "mov"):
        # Text subtitles like subrip/srt/ass/webvtt can be converted to mov_text; bitmap subs cannot
        convertible = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "tx3g"}
        for sub in media.subtitle_streams:
            if sub.codec_name not in convertible:
                warnings.append(
                    f"Subtitle codec '{sub.codec_name}' cannot be stored in .{container} and will be omitted."
                )
                return "drop", warnings
        return "mov_text", []

    if container == "webm":
        for sub in media.subtitle_streams:
            if sub.codec_name not in ("webvtt", "subrip", "srt"):
                warnings.append(
                    f"Subtitle codec '{sub.codec_name}' is not supported in .webm and will be omitted."
                )
                return "drop", warnings
        return "webvtt", []

    for sub in media.subtitle_streams:
        if sub.codec_name not in supported_subs:
            warnings.append(
                f"Subtitle codec '{sub.codec_name}' is not supported in .{container} and will be omitted."
            )
            return "drop", warnings
    return "copy", []


def calculate_target_size_plan(
    media: MediaMetadata,
    target_size_mb: float,
    compatibility_mode: CompatibilityMode = CompatibilityMode.MODERN_ANDROID,
) -> TargetSizePlan:
    """Calculate genuine two-pass video/audio bitrates for Preset E (Custom Target File Size).

    Accounts for:
    - Input duration
    - Audio stream bitrates (preserving audio when reasonable or stepping down when tight)
    - Container muxing overhead (1.5% + 64 KB safety margin)
    - Subtitle/metadata reserve
    - Minimum viable video bitrate & bits-per-pixel-per-frame quality risk assessment
    """
    duration = max(0.1, media.duration_seconds)
    target_bytes = int(round(target_size_mb * 1024 * 1024))

    # Reserve 2% container/muxing overhead + 64 KB header floor
    overhead_bytes = max(65_536, int(target_bytes * 0.02))
    subtitle_reserve_bytes = len(media.subtitle_streams) * int(duration * 1024)  # ~8 kbps per subtitle track

    usable_bytes = target_bytes - overhead_bytes - subtitle_reserve_bytes
    total_usable_bps = int((usable_bytes * 8) / duration) if usable_bytes > 0 else 0

    # Audio budget planning
    num_audio = len(media.audio_streams)
    source_audio_bps = media.total_audio_bitrate_bps if num_audio > 0 else 0
    audio_action = "none"
    planned_audio_bps = 0

    if num_audio > 0:
        # If source audio takes <= 20% of target budget and <= 192 kbps/track, copy or keep high quality
        if source_audio_bps <= total_usable_bps * 0.20 and source_audio_bps <= 192_000 * num_audio:
            planned_audio_bps = source_audio_bps
            audio_action = "copy"
        elif total_usable_bps >= 320_000:
            planned_audio_bps = 128_000 * num_audio
            audio_action = "reencode_128k"
        elif total_usable_bps >= 160_000:
            planned_audio_bps = 96_000 * num_audio
            audio_action = "reencode_96k"
        else:
            planned_audio_bps = 64_000 * num_audio
            audio_action = "reencode_64k"

    required_video_bps = total_usable_bps - planned_audio_bps
    required_video_kbps = required_video_bps // 1000

    primary_v = media.primary_video
    width = primary_v.width if primary_v else 1920
    height = primary_v.height if primary_v else 1080
    fps = primary_v.fps if primary_v and primary_v.fps > 0 else 30.0
    pixels_per_sec = max(1.0, width * height * fps)
    bpp = required_video_bps / pixels_per_sec if required_video_bps > 0 else 0.0
    predicted_ratio = target_bytes / max(1, media.file_size_bytes)

    # Minimum absolute video bitrate threshold: at least 45 kbps and positive budget
    min_viable_kbps = 45
    if target_size_mb <= 0 or usable_bytes <= 0 or required_video_kbps < min_viable_kbps:
        return TargetSizePlan(
            target_bytes=max(0, target_bytes),
            duration_seconds=duration,
            container_overhead_bytes=overhead_bytes,
            subtitle_attachment_reserve_bytes=subtitle_reserve_bytes,
            audio_total_bps=planned_audio_bps,
            audio_codec_action=audio_action,
            required_video_bps=max(0, required_video_bps),
            required_video_kbps=max(0, required_video_kbps),
            predicted_ratio=predicted_ratio,
            bits_per_pixel_frame=max(0.0, bpp),
            is_feasible=False,
            quality_risk_level="impossible",
            warning_message_key="warn_target_impossible",
            explanation=(
                f"Target size ({target_size_mb:.2f} MB) is too small for a {duration:.1f}s media file "
                f"once audio ({planned_audio_bps // 1000} kbps) and container overhead are reserved."
            ),
        )

    # Evaluate visual degradation risk based on codec efficiency (HEVC vs H.264)
    high_risk_bpp = 0.018 if compatibility_mode == CompatibilityMode.MODERN_ANDROID else 0.028
    mod_risk_bpp = 0.035 if compatibility_mode == CompatibilityMode.MODERN_ANDROID else 0.050

    if bpp < high_risk_bpp or required_video_kbps < 150:
        risk_level = "high"
        warn_key = "warn_target_high_risk"
    elif bpp < mod_risk_bpp or required_video_kbps < 350:
        risk_level = "moderate"
        warn_key = "warn_target_moderate_risk"
    else:
        risk_level = "low"
        warn_key = None

    return TargetSizePlan(
        target_bytes=target_bytes,
        duration_seconds=duration,
        container_overhead_bytes=overhead_bytes,
        subtitle_attachment_reserve_bytes=subtitle_reserve_bytes,
        audio_total_bps=planned_audio_bps,
        audio_codec_action=audio_action,
        required_video_bps=required_video_bps,
        required_video_kbps=required_video_kbps,
        predicted_ratio=predicted_ratio,
        bits_per_pixel_frame=round(bpp, 5),
        is_feasible=True,
        quality_risk_level=risk_level,
        warning_message_key=warn_key,
        explanation=(
            f"Two-pass target plan: {required_video_kbps} kbps video + "
            f"{planned_audio_bps // 1000} kbps audio + {overhead_bytes // 1024} KB container reserve "
            f"(~{predicted_ratio * 100:.1f}% of original size). Exact output size is not guaranteed."
        ),
    )
