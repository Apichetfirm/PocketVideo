#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

FFMPEG="$(command -v ffmpeg || true)"
FFPROBE="$(command -v ffprobe || true)"
if [[ ! -x "$FFMPEG" || ! -x "$FFPROBE" ]]; then
  echo "FFmpeg and FFprobe must be installed, e.g. brew install ffmpeg" >&2
  exit 1
fi
python packaging/generate_icons.py
test -s assets/pocketvideo.icns
python -m PyInstaller --noconfirm --clean --windowed --onedir \
  --icon assets/pocketvideo.icns \
  --osx-bundle-identifier app.firmimpulse.pocketvideo \
  --add-data "assets/pocketvideo.png:assets" \
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
# Local development/QA signature only; NOT a Developer ID or notarization.
codesign --deep --force --sign - dist/PocketVideo.app
codesign --verify --deep --strict --verbose=2 dist/PocketVideo.app
mkdir -p dist/macos-alpha
MACHINE_ARCH="$(uname -m)"
ditto -c -k --sequesterRsrc --keepParent dist/PocketVideo.app \
  "dist/macos-alpha/PocketVideo-1.3.1-macos-${MACHINE_ARCH}-alpha.zip"
shasum -a 256 dist/macos-alpha/*.zip > dist/macos-alpha/SHA256SUMS.txt
