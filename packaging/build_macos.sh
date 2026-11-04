#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

FFMPEG="$(command -v ffmpeg || true)"
FFPROBE="$(command -v ffprobe || true)"
if [[ ! -x "$FFMPEG" || ! -x "$FFPROBE" ]]; then
  echo "FFmpeg and FFprobe must be installed, e.g. brew install ffmpeg" >&2
  exit 1
fi
python -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name PocketVideo \
  --add-data "LICENSE:." \
  --add-data "THIRD_PARTY_NOTICES.md:." \
  --add-data "README.md:." \
  --add-binary "$FFMPEG:bin" \
  --add-binary "$FFPROBE:bin" \
  --collect-all pillow_heif \
  --hidden-import PySide6.QtMultimedia \
  --hidden-import PySide6.QtMultimediaWidgets \
  src/pocketvideo/main.py

if [[ ! -d dist/PocketVideo.app ]]; then
  echo "Expected macOS .app bundle missing" >&2
  exit 1
fi
# Apple ad-hoc testing signature only. No Developer ID or notarization.
codesign --deep --force --sign - dist/PocketVideo.app
mkdir -p dist/macos-alpha
ditto -c -k --sequesterRsrc --keepParent dist/PocketVideo.app \
  dist/macos-alpha/PocketVideo-1.3.0-macos-alpha.zip
shasum -a 256 dist/macos-alpha/*.zip > dist/macos-alpha/SHA256SUMS.txt
