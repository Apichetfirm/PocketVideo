#!/usr/bin/env python3
"""Reject unbundled Homebrew FFmpeg dependencies in the macOS .app.

Running an .app successfully on the same Homebrew-equipped CI host does NOT
prove the app will start on a clean Mac. Inspect all bundled dylib references
to detect absolute paths into /opt/homebrew or /usr/local/Cellar.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


BREW_PREFIXES = ("/opt/homebrew/", "/usr/local/Cellar/", "/usr/local/opt/")


def audit_bundle(app: Path) -> list[str]:
    issues = []
    if not app.is_dir():
        return [f"Missing macOS app bundle: {app}"]
    candidates = [
        path for path in app.rglob("*")
        if path.is_file()
        and (path.suffix in (".dylib", ".so")
             or path.name in ("ffmpeg", "ffprobe"))
    ]
    ffmpeg_files = [x for x in candidates if x.name == "ffmpeg"]
    ffprobe_files = [x for x in candidates if x.name == "ffprobe"]
    if not ffmpeg_files or not ffprobe_files:
        issues.append("FFmpeg and FFprobe executables must both be inside the .app bundle.")
    for path in candidates:
        run = subprocess.run(
            ["otool", "-L", str(path)],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if run.returncode != 0:
            issues.append(f"otool could not inspect {path}: {run.stderr[:200]}")
            continue
        for line in run.stdout.splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith(BREW_PREFIXES):
                issues.append(f"{path}: unresolved external Homebrew dependency {dependency}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", nargs="?", default="dist/PocketVideo.app")
    args = parser.parse_args()
    issues = audit_bundle(Path(args.app))
    for issue in issues:
        print("BUNDLE DEPENDENCY:", issue)
    if issues:
        print(f"FAILED: {len(issues)} missing relocations/dependencies.")
        return 1
    print("Bundle audit passed: no direct absolute Homebrew FFmpeg dylib paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
