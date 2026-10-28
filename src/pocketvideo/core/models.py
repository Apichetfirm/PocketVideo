"""Domain models and enums for PocketVideo."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
import time
import uuid


class PresetId(str, Enum):
    """Five primary user-facing compression presets."""
    BEST_QUALITY = "preset_a_best_quality"
    BEST_BALANCE = "preset_b_best_balance"
    MAX_SPACE_SAVING = "preset_c_max_saving"
    FASTEST = "preset_d_fastest"
    CUSTOM_SIZE = "preset_e_custom_size"


class CompatibilityMode(str, Enum):
    """Android output compatibility target."""
    MODERN_ANDROID = "modern_android"        # H.265 / HEVC in MP4 (or VP9/AV1 in WebM/MKV)
    MAX_COMPATIBILITY = "max_compatibility"  # H.264 / AAC in MP4


class OperationMode(str, Enum):
    """Distinguishes compression, full transcoding, and lossless smart remux."""
    COMPRESS = "compress"
    CONVERT_TRANSCODE = "convert_transcode"
    SMART_REMUX = "smart_remux"


class ResourceMode(str, Enum):
    """Simple user-facing resource usage modes."""
    LIGHT = "light"
    BALANCED = "balanced"
    MAXIMUM = "maximum"
    CUSTOM = "custom"


class ProcessPriority(str, Enum):
    """OS process scheduling priority."""
    IDLE = "idle"
    BELOW_NORMAL = "below_normal"
    NORMAL = "normal"
    ABOVE_NORMAL = "above_normal"


class HdrPolicy(str, Enum):
    """Policy for handling HDR10 / HLG / Dolby Vision streams."""
    PRESERVE_HDR = "preserve_hdr"
    SAFE_COPY_OR_WARN = "safe_copy_or_warn"


class JobStatus(str, Enum):
    """Lifecycle states of a processing job in the queue."""
    QUEUED = "queued"
    VALIDATING = "validating"
    PASS_1 = "pass_1"
    PROCESSING = "processing"
    VERIFYING = "verifying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class VideoStreamInfo:
    """Metadata for a single video stream extracted via FFprobe."""
    index: int
    codec_name: str
    codec_long_name: str
    profile: str
    width: int
    height: int
    fps: float
    avg_frame_rate_raw: str
    r_frame_rate_raw: str
    is_vfr: bool
    bit_rate: Optional[int]
    pix_fmt: str
    bit_depth: int
    rotation: int
    color_space: Optional[str] = None
    color_transfer: Optional[str] = None
    color_primaries: Optional[str] = None
    is_hdr: bool = False
    is_dolby_vision: bool = False
    hdr_format_name: str = "SDR"

    @property
    def effective_width(self) -> int:
        return self.height if abs(self.rotation) in (90, 270) else self.width

    @property
    def effective_height(self) -> int:
        return self.width if abs(self.rotation) in (90, 270) else self.height

    @property
    def is_portrait(self) -> bool:
        return self.effective_height > self.effective_width

    @property
    def resolution_label(self) -> str:
        return f"{self.effective_width}x{self.effective_height}"


@dataclass(frozen=True)
class AudioStreamInfo:
    """Metadata for a single audio stream extracted via FFprobe."""
    index: int
    codec_name: str
    channels: int
    sample_rate: int
    bit_rate: Optional[int]
    language: Optional[str] = None
    title: Optional[str] = None


@dataclass(frozen=True)
class SubtitleStreamInfo:
    """Metadata for a single subtitle stream extracted via FFprobe."""
    index: int
    codec_name: str
    language: Optional[str] = None
    title: Optional[str] = None


@dataclass
class MediaMetadata:
    """Complete media inspection result from FFprobe."""
    file_path: Path
    container_format: str
    duration_seconds: float
    file_size_bytes: int
    overall_bit_rate: int
    video_streams: list[VideoStreamInfo] = field(default_factory=list)
    audio_streams: list[AudioStreamInfo] = field(default_factory=list)
    subtitle_streams: list[SubtitleStreamInfo] = field(default_factory=list)
    creation_time: Optional[str] = None
    android_make: Optional[str] = None
    android_model: Optional[str] = None
    already_efficient: bool = False
    efficiency_warning_key: Optional[str] = None
    hdr_warning_key: Optional[str] = None
    raw_tags: dict[str, str] = field(default_factory=dict)

    @property
    def primary_video(self) -> Optional[VideoStreamInfo]:
        return self.video_streams[0] if self.video_streams else None

    @property
    def primary_audio(self) -> Optional[AudioStreamInfo]:
        return self.audio_streams[0] if self.audio_streams else None

    @property
    def total_audio_bitrate_bps(self) -> int:
        total = 0
        for stream in self.audio_streams:
            if stream.bit_rate and stream.bit_rate > 0:
                total += stream.bit_rate
            else:
                # Conservative fallback estimate of 128 kbps per audio stream
                total += 128_000
        return total


@dataclass
class ResourceLimits:
    """Resource management configuration distinguishing hard limits from best-effort controls."""
    mode: ResourceMode = ResourceMode.BALANCED
    # Hard limit enforced on Windows via Job Object JOBOBJECT_CPU_RATE_CONTROL_INFORMATION (1-100%)
    cpu_hard_cap_percent: int = 75
    enable_cpu_hard_cap: bool = True
    # Best-effort controls
    cpu_threads: int = 0  # 0 = auto-calculated from mode
    cpu_affinity_mask: Optional[int] = None
    process_priority: ProcessPriority = ProcessPriority.BELOW_NORMAL
    max_concurrent_jobs: int = 1
    enable_gpu_encoding: bool = False
    preferred_gpu_encoder: str = "auto"  # "auto", "nvenc", "qsv", "amf"


@dataclass
class AdvancedEncodingOptions:
    """Optional overrides from the expandable Advanced Settings panel."""
    override_enabled: bool = False
    video_codec: Optional[str] = None        # e.g., "h264", "hevc", "vp9", "av1"
    encoder_name: Optional[str] = None       # e.g., "libx264", "libx265", "h264_nvenc"
    crf: Optional[int] = None                # 0-51
    video_bitrate_kbps: Optional[int] = None
    max_width: Optional[int] = None
    max_height: Optional[int] = None
    target_fps: Optional[float] = None
    encoder_speed_preset: Optional[str] = None  # "ultrafast" .. "veryslow"
    audio_codec: Optional[str] = None        # "copy", "aac", "libopus", "mp3"
    audio_bitrate_kbps: Optional[int] = None
    force_two_pass: bool = False
    preserve_metadata: bool = True
    preserve_subtitles: bool = True


@dataclass
class TargetSizePlan:
    """Result of two-pass target file size calculation (Preset E)."""
    target_bytes: int
    duration_seconds: float
    container_overhead_bytes: int
    subtitle_attachment_reserve_bytes: int
    audio_total_bps: int
    audio_codec_action: str  # "copy" or "reencode"
    required_video_bps: int
    required_video_kbps: int
    predicted_ratio: float   # target_bytes / original_bytes
    bits_per_pixel_frame: float
    is_feasible: bool
    quality_risk_level: str  # "low", "moderate", "high", "impossible"
    warning_message_key: Optional[str] = None
    explanation: str = ""


@dataclass
class EncodingJobSpec:
    """Complete specification for a single compression or conversion job."""
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    input_path: Path = field(default_factory=Path)
    output_path: Path = field(default_factory=Path)
    operation_mode: OperationMode = OperationMode.COMPRESS
    preset_id: PresetId = PresetId.BEST_BALANCE
    compatibility_mode: CompatibilityMode = CompatibilityMode.MODERN_ANDROID
    target_container: str = "mp4"
    target_size_mb: Optional[float] = None
    allow_smart_remux: bool = True
    advanced: AdvancedEncodingOptions = field(default_factory=AdvancedEncodingOptions)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)


@dataclass
class JobProgress:
    """Real-time progress telemetry parsed from FFmpeg -progress pipe:1."""
    pass_number: int = 1
    total_passes: int = 1
    out_time_seconds: float = 0.0
    duration_seconds: float = 0.0
    percent: float = 0.0
    fps: float = 0.0
    speed_multiplier: float = 0.0
    bitrate_kbps: float = 0.0
    current_size_bytes: int = 0
    elapsed_seconds: float = 0.0
    eta_seconds: Optional[float] = None


@dataclass
class JobResultComparison:
    """Verified post-encode comparison metrics calculated from actual completed files."""
    job_id: str
    input_path: Path
    output_path: Path
    operation_mode: OperationMode
    original_size_bytes: int
    output_size_bytes: int
    space_saved_bytes: int
    reduction_percent: float
    original_resolution: str
    output_resolution: str
    original_codec: str
    output_codec: str
    processing_duration_seconds: float
    completed_at_epoch: float = field(default_factory=time.time)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "operation_mode": self.operation_mode.value,
            "original_size_bytes": self.original_size_bytes,
            "output_size_bytes": self.output_size_bytes,
            "space_saved_bytes": self.space_saved_bytes,
            "reduction_percent": round(self.reduction_percent, 2),
            "original_resolution": self.original_resolution,
            "output_resolution": self.output_resolution,
            "original_codec": self.original_codec,
            "output_codec": self.output_codec,
            "processing_duration_seconds": round(self.processing_duration_seconds, 2),
            "completed_at_epoch": self.completed_at_epoch,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobResultComparison":
        return cls(
            job_id=str(data.get("job_id", "")),
            input_path=Path(data.get("input_path", "")),
            output_path=Path(data.get("output_path", "")),
            operation_mode=OperationMode(data.get("operation_mode", OperationMode.COMPRESS.value)),
            original_size_bytes=int(data.get("original_size_bytes", 0)),
            output_size_bytes=int(data.get("output_size_bytes", 0)),
            space_saved_bytes=int(data.get("space_saved_bytes", 0)),
            reduction_percent=float(data.get("reduction_percent", 0.0)),
            original_resolution=str(data.get("original_resolution", "")),
            output_resolution=str(data.get("output_resolution", "")),
            original_codec=str(data.get("original_codec", "")),
            output_codec=str(data.get("output_codec", "")),
            processing_duration_seconds=float(data.get("processing_duration_seconds", 0.0)),
            completed_at_epoch=float(data.get("completed_at_epoch", 0.0)),
            warnings=list(data.get("warnings", [])),
        )
