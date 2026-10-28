"""FFmpeg and FFprobe binary resolution, validation, and encoder capability detection."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional


class MediaBinaryError(RuntimeError):
    """Raised when FFmpeg or FFprobe is missing, broken, or non-executable."""


@dataclass
class BinaryPaths:
    """Resolved and verified paths for FFmpeg and FFprobe."""
    ffmpeg: Path
    ffprobe: Path
    version_banner: str = ""
    build_configuration: str = ""
    is_nonfree: bool = False
    available_video_encoders: set[str] = field(default_factory=set)
    available_audio_encoders: set[str] = field(default_factory=set)


def _get_CreationFlags() -> int:
    """Suppress console window flashing when spawning subprocesses on Windows."""
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def resolve_binaries(
    custom_ffmpeg: Optional[str | Path] = None,
    custom_ffprobe: Optional[str | Path] = None,
) -> BinaryPaths:
    """Locate bundled, custom, or system FFmpeg and FFprobe binaries and validate them."""
    exe_suffix = ".exe" if os.name == "nt" else ""
    candidates_dirs: list[Path] = []

    # 1. PyInstaller bundle directory (_MEIPASS)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates_dirs.append(Path(meipass) / "bin")
        candidates_dirs.append(Path(meipass))

    # 2. Application executable directory & repository vendor/bin directory
    app_dir = Path(sys.executable).resolve().parent
    repo_root = Path(__file__).resolve().parents[3]
    candidates_dirs.extend([
        app_dir / "bin",
        app_dir / "ffmpeg",
        app_dir,
        repo_root / "vendor" / "ffmpeg" / "bin",
        repo_root / "bin",
    ])

    ffmpeg_path = _find_single_binary("ffmpeg" + exe_suffix, custom_ffmpeg, candidates_dirs)
    ffprobe_path = _find_single_binary("ffprobe" + exe_suffix, custom_ffprobe, candidates_dirs)

    return validate_binaries(ffmpeg_path, ffprobe_path)


def _find_single_binary(
    binary_name: str,
    custom_path: Optional[str | Path],
    search_dirs: list[Path],
) -> Path:
    if custom_path and str(custom_path).strip():
        candidate = Path(custom_path).expanduser().resolve()
        if not candidate.is_file():
            raise MediaBinaryError(f"Configured binary does not exist: {candidate}")
        return candidate

    for folder in search_dirs:
        candidate = folder / binary_name
        if candidate.is_file():
            return candidate.resolve()

    base_stem = binary_name[:-4] if binary_name.endswith(".exe") else binary_name
    which_hit = shutil.which(binary_name) or shutil.which(base_stem)
    if which_hit:
        return Path(which_hit).resolve()

    raise MediaBinaryError(
        f"Required media binary '{binary_name}' was not found in the application bundle or system PATH."
    )


def validate_binaries(ffmpeg_path: Path, ffprobe_path: Path) -> BinaryPaths:
    """Run health checks on FFmpeg and FFprobe and inspect supported encoders."""
    creation_flags = _get_CreationFlags()
    for label, path in (("FFmpeg", ffmpeg_path), ("FFprobe", ffprobe_path)):
        if not path.exists() or not path.is_file():
            raise MediaBinaryError(f"{label} binary is missing at: {path}")
        try:
            proc = subprocess.run(
                [str(path), "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MediaBinaryError(f"Failed to execute {label} binary ({path}): {exc}") from exc

        if proc.returncode != 0 or "version" not in proc.stdout.lower():
            raise MediaBinaryError(
                f"{label} binary ({path}) returned invalid output (exit {proc.returncode})."
            )

        if label == "FFmpeg":
            lines = proc.stdout.splitlines()
            version_banner = lines[0] if lines else ""
            config_line = next(
                (line for line in lines if "configuration:" in line), ""
            )
            is_nonfree = "--enable-nonfree" in config_line

    # Query available encoders from FFmpeg
    video_encoders: set[str] = set()
    audio_encoders: set[str] = set()
    try:
        enc_proc = subprocess.run(
            [str(ffmpeg_path), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creation_flags,
        )
        if enc_proc.returncode == 0:
            for line in enc_proc.stdout.splitlines():
                stripped = line.strip()
                if len(stripped) > 8 and stripped[0] in ("V", "A") and stripped[6] == " ":
                    parts = stripped.split()
                    if len(parts) >= 2:
                        kind = stripped[0]
                        enc_name = parts[1]
                        if kind == "V":
                            video_encoders.add(enc_name)
                        elif kind == "A":
                            audio_encoders.add(enc_name)
    except (OSError, subprocess.SubprocessError):
        pass

    return BinaryPaths(
        ffmpeg=ffmpeg_path,
        ffprobe=ffprobe_path,
        version_banner=version_banner,
        build_configuration=config_line,
        is_nonfree=is_nonfree,
        available_video_encoders=video_encoders,
        available_audio_encoders=audio_encoders,
    )


def detect_working_gpu_encoder(
    binaries: BinaryPaths,
    target_codec: str = "hevc",
    preferred_vendor: str = "auto",
) -> Optional[str]:
    """Probe whether a hardware GPU encoder (NVENC, QSV, AMF) actually initializes."""
    codec_prefix = "hevc" if target_codec in ("hevc", "h265") else "h264"
    vendor_order = ["nvenc", "qsv", "amf"]
    if preferred_vendor in vendor_order:
        vendor_order = [preferred_vendor] + [v for v in vendor_order if v != preferred_vendor]

    creation_flags = _get_CreationFlags()
    for vendor in vendor_order:
        candidate = f"{codec_prefix}_{vendor}"
        if candidate not in binaries.available_video_encoders:
            continue
        # Perform a fast 1-frame null encode test to verify hardware driver availability
        try:
            test_cmd = [
                str(binaries.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=256x256:d=0.04",
                "-frames:v",
                "1",
                "-c:v",
                candidate,
                "-f",
                "null",
                "-",
            ]
            res = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creation_flags,
            )
            if res.returncode == 0:
                return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    return None
