# Third-Party Licenses & Source Availability Notices

PocketVideo is licensed under the **GNU General Public License v3.0 or later (`GPL-3.0-or-later`)**, which is fully compatible with all bundled and linked open-source components listed below.

## 1. FFmpeg & FFprobe
- **Website**: https://ffmpeg.org/
- **Windows Redistributable Builds**: https://github.com/BtbN/FFmpeg-Builds
- **License**: GNU General Public License Version 3 (`GPLv3`)
- **Build Configuration Policy**:
  PocketVideo bundles pinned, cryptographically verified (`SHA-256`) Windows x64 builds of `ffmpeg.exe` and `ffprobe.exe` compiled under `--enable-gpl` and **never** `--enable-nonfree`. Our build pipeline (`packaging/fetch_ffmpeg_windows.py`) programmatically inspects `ffmpeg -version` and rejects any binary compiled with `--enable-nonfree`.
- **Source Code Availability**:
  Corresponding source code for the bundled FFmpeg build is available from:
  - Official FFmpeg Git repository: https://git.ffmpeg.org/ffmpeg.git
  - BtbN build scripts and exact commit tags: https://github.com/BtbN/FFmpeg-Builds

## 2. PySide6 / Qt for Python (Qt 6)
- **Website**: https://www.qt.io/qt-for-python
- **License**: GNU Lesser General Public License Version 3 (`LGPLv3`) / `GPLv3`
- **Dynamic Linking & Notice**:
  PocketVideo uses unmodified PySide6 / Qt 6 shared libraries dynamically linked at runtime. Users are free to inspect or replace the dynamically linked Qt 6 shared libraries inside the application directory. Source code for Qt 6 and PySide6 is available at https://code.qt.io/.

## 3. Python Runtime & Standard Library
- **Website**: https://www.python.org/
- **License**: Python Software Foundation License Version 2 (`PSF-2.0`)
