# PocketVideo macOS Alpha — Developer Testing Only

Current state: an **early native macOS port**. No Developer ID signature,
Apple notarization, official Apple Silicon / Intel QA certification, or legal
rights signoff for redistribution of FFmpeg/HEVC/libheif binary dependencies.

## Local run on a macOS developer machine
1. Install Python >=3.11 and FFmpeg/FFprobe with Homebrew: `brew install ffmpeg`.
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `python -m pip install -e .`
4. `python -m pocketvideo.main`
5. For local .app building only, run `bash packaging/build_macos.sh`
   after installing `pyinstaller` in the virtual environment.

## Platform differences
- Apple VideoToolbox is attempted only if present in the local FFmpeg build and
  a real encoder probe passes; NVIDIA/Intel/AMD accelerators depend on platform.
- No Windows Job Object hard CPU cap on macOS; best-effort FFmpeg threads and
  process priority remain available.
- Finder `open -R` selects files when they exist.
- After-export Sleep/Shutdown is **disabled** until a confirmed native macOS
  implementation is independently tested.
- macOS writes settings to `~/Library/Application Support/PocketVideo`
  and defaults to `~/Movies/PocketVideo_Output`.
- `pillow-heif` on macOS depends on available binary wheel and bundled codec
  compatibility. HEIC support is tested separately where possible.

## Known risks
- FFmpeg/Homebrew libraries and codec redistribution rights require legal
  review before distributing the .app. A CI artifact is not a public release.
- Apple Silicon/Intel hardware tests, high-bitrate 4K HDR playback/color
  accuracy, prolonged VideoToolbox stress, USB unplug and power loss behavior
  remain outside CI coverage. User files: keep both and back up originals.
- App bundle uses ad-hoc signature for testing, not Apple notarization.


## macOS v1.3.1 build quality gates
- New CI runs separately on `macos-15` Apple Silicon and `macos-15-intel`
  and exercises packaged video compression without Homebrew on PATH.
- The branded `.app` is **ad-hoc signed only** until a configured Developer
  ID signing/notarization workflow succeeds.
- See `docs/MACOS_DISTRIBUTION.md` for setup, test matrix and release gates.
