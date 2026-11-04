#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

FFMPEG="$(command -v ffmpeg || true)"
FFPROBE="$(command -v ffprobe || true)"
if [[ ! -x "$FFMPEG" || ! -x "$FFPROBE" ]]; then
  echo "FFmpeg and FFprobe must be installed, e.g. brew install ffmpeg" >&2
  exit 1
fi
if ! command -v dylibbundler >/dev/null 2>&1; then
  echo "dylibbundler is required to isolate FFmpeg libraries: brew install dylibbundler" >&2
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

# Qt Multimedia bundles its own libavcodec/libavutil, which may have the SAME
# soname but DIFFERENT ABI from Homebrew FFmpeg. PyInstaller's normal
# collection merges those two names and FFmpeg can SIGABRT on clean macOS.
# Replace only the app's FFmpeg/FFprobe executables with the real Homebrew
# originals, then collect their full dependency graph into an isolated folder.
# dylibbundler rewrites their load commands to point to this private folder
# instead of resolving Qt's incompatible libavcodec.
APP_FRAMEWORKS="dist/PocketVideo.app/Contents/Frameworks"
APP_FFMPEG="$(find "$APP_FRAMEWORKS" -type f -name ffmpeg -print -quit)"
APP_FFPROBE="$(find "$APP_FRAMEWORKS" -type f -name ffprobe -print -quit)"
test -n "$APP_FFMPEG" && test -n "$APP_FFPROBE"
cp -f "$FFMPEG" "$APP_FFMPEG"
cp -f "$FFPROBE" "$APP_FFPROBE"
chmod +x "$APP_FFMPEG" "$APP_FFPROBE"
dylibbundler -b -cd -of \
  -x "$APP_FFMPEG" -x "$APP_FFPROBE" \
  -d "$APP_FRAMEWORKS/ffmpeg_libs" \
  -p "@executable_path/../ffmpeg_libs/"
test -n "$(find "$APP_FRAMEWORKS/ffmpeg_libs" -type f -name '*.dylib' -print -quit)"

# Local development/QA signature only; NOT a Developer ID or notarization.
codesign --deep --force --sign - dist/PocketVideo.app
codesign --verify --deep --strict --verbose=2 dist/PocketVideo.app
mkdir -p dist/macos-alpha
MACHINE_ARCH="$(uname -m)"
ditto -c -k --sequesterRsrc --keepParent dist/PocketVideo.app \
  "dist/macos-alpha/PocketVideo-1.3.2-macos-${MACHINE_ARCH}-alpha.zip"
shasum -a 256 dist/macos-alpha/*.zip > dist/macos-alpha/SHA256SUMS.txt
