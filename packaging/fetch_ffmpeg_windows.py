"""Download and cryptographically verify redistributable FFmpeg/FFprobe Windows x64 binaries.

Security & Licensing Policy:
- Uses a pinned release URL from the official BtbN/FFmpeg-Builds GitHub releases (GPLv3 redistributable,
  explicitly NOT marked `--enable-nonfree`).
- Enforces SHA-256 checksum verification before extracting `ffmpeg.exe`, `ffprobe.exe`, and `LICENSE.txt`.
- Also verifies that the extracted binary does not contain `--enable-nonfree` in its build configuration.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

PINNED_FFMPEG_VERSION = "N-128121-g86444dd368-win64-gpl"
PINNED_FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "autobuild-2026-10-27-18-38/ffmpeg-N-128121-g86444dd368-win64-gpl.zip"
)
# Official GitHub release asset ID 682510708, digest from the BtbN release API.
# This is a pinned, static GPL Windows x64 build (not a moving "latest" URL).
PINNED_FFMPEG_SHA256 = (
    "1eb70a550388567bc2abbb2d164814f722f36a034fd927dcdfb217bc7e94930c"
)


def compute_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def verify_checksum(file_path: Path, expected_sha256: str) -> None:
    actual = compute_sha256(file_path)
    expected = expected_sha256.strip().lower()
    if actual != expected:
        raise ValueError(
            f"SHA-256 verification failed for {file_path.name}!\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {actual}"
        )


def verify_redistributable_configuration(ffmpeg_exe: Path) -> str:
    """Inspect `ffmpeg -version` to confirm `--enable-nonfree` is NOT present."""
    proc = subprocess.run(
        [str(ffmpeg_exe), "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg binary failed health check: {proc.stderr}")
    output = proc.stdout
    if "--enable-nonfree" in output:
        raise PermissionError(
            "License violation: FFmpeg binary is compiled with --enable-nonfree and is NOT redistributable!"
        )
    return output


def extract_verified_archive(archive_path: Path, target_vendor_dir: Path) -> None:
    bin_dir = target_vendor_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.namelist():
            lower = member.lower()
            if lower.endswith("/bin/ffmpeg.exe"):
                with zf.open(member) as src, (bin_dir / "ffmpeg.exe").open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            elif lower.endswith("/bin/ffprobe.exe"):
                with zf.open(member) as src, (bin_dir / "ffprobe.exe").open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            elif lower.endswith("license.txt") or lower.endswith("copying.gplv3"):
                out_lic = target_vendor_dir / Path(member).name
                with zf.open(member) as src, out_lic.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and verify Windows x64 FFmpeg binaries")
    parser.add_argument("--url", default=PINNED_FFMPEG_URL)
    parser.add_argument("--sha256", default=PINNED_FFMPEG_SHA256)
    parser.add_argument(
        "--vendor-dir",
        default=str(Path(__file__).resolve().parents[1] / "vendor" / "ffmpeg"),
    )
    parser.add_argument("--local-archive", default="", help="Use pre-downloaded zip archive")
    args = parser.parse_args(argv)

    vendor_dir = Path(args.vendor_dir).resolve()
    vendor_dir.mkdir(parents=True, exist_ok=True)
    archive_path = (
        Path(args.local_archive).resolve()
        if args.local_archive
        else vendor_dir / "ffmpeg-win64-gpl.zip"
    )

    if not args.local_archive:
        print(f"Downloading pinned FFmpeg archive: {args.url}")
        urllib.request.urlretrieve(args.url, archive_path)

    print(f"Verifying SHA-256 checksum ({args.sha256})...")
    verify_checksum(archive_path, args.sha256)
    print("SHA-256 checksum verified.")

    extract_verified_archive(archive_path, vendor_dir)
    ffmpeg_exe = vendor_dir / "bin" / "ffmpeg.exe"
    if sys.platform == "win32" and ffmpeg_exe.exists():
        verify_redistributable_configuration(ffmpeg_exe)
        print("Verified FFmpeg build configuration is redistributable (no --enable-nonfree).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
