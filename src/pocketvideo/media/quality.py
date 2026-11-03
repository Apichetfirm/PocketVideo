"""Optional sampled SSIM / VMAF quality evidence for real Before/After videos.

Never changes source/export files. Comparison is sampled at 8 fps for at most
six seconds and normalized to 640x360, so numbers are *diagnostics*, not a
full-duration perceptual-quality guarantee. VMAF is optional and automatically
skipped unless the bundled FFmpeg explicitly supports libvmaf.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile

from pocketvideo.media.binaries import BinaryPaths, _get_CreationFlags


class QualityAnalysisError(RuntimeError):
    pass


def has_vmaf_filter(binaries: BinaryPaths) -> bool:
    try:
        p = subprocess.run(
            [str(binaries.ffmpeg), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=12,
            encoding="utf-8", errors="replace",
            creationflags=_get_CreationFlags(),
        )
        return p.returncode == 0 and bool(
            re.search(r"\s+libvmaf\s+", p.stdout)
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def analyze_video_quality(
    before: Path, after: Path, binaries: BinaryPaths,
    *, before_offset_seconds: float = 0.0,
    max_seconds: float = 6.0, include_vmaf: bool = True,
) -> dict[str, float | None]:
    before = Path(before)
    after = Path(after)
    if not before.is_file() or not after.is_file():
        raise FileNotFoundError("Before or After video is unavailable.")
    samples = max(1.0, min(10.0, float(max_seconds)))
    normalizer = (
        "fps=8,scale=640:360:force_original_aspect_ratio=decrease,"
        "pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        "format=yuv420p,setpts=PTS-STARTPTS"
    )
    base = [
        str(binaries.ffmpeg), "-hide_banner", "-nostdin", "-v", "info",
        "-ss", f"{max(0.0, before_offset_seconds):.3f}",
        "-t", f"{samples:.3f}", "-i", str(before),
        "-t", f"{samples:.3f}", "-i", str(after),
    ]

    def run_filter(last_filter: str, cwd: Path) -> str:
        graph = (
            f"[0:v]{normalizer}[ref];"
            f"[1:v]{normalizer}[dist];"
            f"[dist][ref]{last_filter}"
        )
        try:
            p = subprocess.run(
                base + ["-filter_complex", graph, "-an", "-f", "null", "-"],
                cwd=str(cwd), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=120, creationflags=_get_CreationFlags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise QualityAnalysisError("Quality analysis timed out.") from exc
        if p.returncode != 0:
            raise QualityAnalysisError(p.stderr[-1800:])
        return p.stderr

    with tempfile.TemporaryDirectory(prefix="pocketvideo_quality_") as td:
        folder = Path(td)
        output = run_filter("ssim", folder)
        hits = re.findall(r"All:([0-9.]+)", output)
        if not hits:
            raise QualityAnalysisError("FFmpeg did not report a valid SSIM result.")
        score = float(hits[-1])
        value: dict[str, float | None] = {"ssim": score, "vmaf": None}
        if include_vmaf and has_vmaf_filter(binaries):
            # A relative filename avoids Windows drive-colon escaping issues
            # inside FFmpeg's libvmaf filter graph.
            try:
                run_filter("libvmaf=log_fmt=json:log_path=vmaf.json", folder)
                metrics = json.loads((folder / "vmaf.json").read_text(encoding="utf-8"))
                value["vmaf"] = float(metrics["pooled_metrics"]["vmaf"]["mean"])
            except (OSError, KeyError, ValueError, QualityAnalysisError):
                value["vmaf"] = None
        return value
