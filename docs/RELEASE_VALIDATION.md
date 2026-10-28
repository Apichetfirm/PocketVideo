# PocketVideo v1.0.0 — Release Validation Status

This document separates tests that were executed from tests that remain to be run. Do not describe a Windows release as verified until the Windows CI checks below succeed.

## Verified in Linux source environment (2026-10-28)

- ZIP archive integrity verified before editing.
- FFmpeg and FFprobe integration smoke tests, portrait video, two-pass encoding, Unicode paths, remux/transcode, output protection, queue cancellation, and settings tests passed.
- `PYTHONPATH=src python -m unittest discover -s tests -v`: **17 tests discovered, 15 passed, 2 skipped** (PySide6 native UI unavailable and real Win32 APIs unavailable).
- `python -m compileall -q src tests scripts packaging`: passed.
- `git diff --check`: passed.

## Required on Windows 10/11 x64 before public release

- [ ] PySide6 offscreen integration test: resource controls change the controller and a new job's settings.
- [ ] Real Win32 Job Object integration test: FFmpeg-like subprocess successfully joins a CPU-capped job and closes cleanly.
- [ ] Pinned FFmpeg download URL and SHA-256 hash verified by the build (unverified in Linux due network restrictions).
- [ ] PyInstaller produces a working `PocketVideo.exe` containing FFmpeg/FFprobe.
- [ ] Smoke test runs the packaged `.exe` and verifies the compressed output with bundled FFprobe.
- [ ] Inno Setup compiles the actual installer, and both installer and portable ZIP are non-empty.
- [ ] Native Windows/PySide6 screenshots reviewed. The screenshots committed under `docs/screenshots/` are design previews, not native captures.
- [ ] Install/uninstall on a clean Windows 10/11 x64 machine, launch with no system FFmpeg installation, and test an actual Android video including an HDR sample where possible.
- [ ] Check bundled FFmpeg's actual license/build configuration and accompany binaries with appropriate notices and source availability.

## Publication sequence

1. Create a **new** public `Apichetfirm/PocketVideo` repository (do not push into an unrelated existing repository).
2. Push this source to its default branch **without a release tag**.
3. Let `.github/workflows/windows-build-release.yml` run on Windows. Inspect test logs, native UI screenshot artifacts, and portable/installer artifacts.
4. Address any CI or real-Windows failures before publishing anything as stable.
5. Only after Windows validation, push tag `v1.0.0`, let Actions publish the GitHub Release, and independently download/verify the release artifacts.

## Known limitations

- No native Windows build or installer was produced in the Linux source environment.
- Two Windows-dependent tests were skipped locally and are mandatory on the Windows runner.
- The bundled FFmpeg URL/hash could not be independently checked locally; a failed SHA check must stop the release rather than be bypassed.
- GPU acceleration support depends on compatible hardware, drivers, and the FFmpeg build and requires real-device verification.
