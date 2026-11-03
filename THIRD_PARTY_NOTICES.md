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


## Pillow / pillow-heif image support (v1.1+)
- Pillow: HPND license; https://python-pillow.github.io/
- pillow-heif 1.8.1: source BSD-3-Clause; **binary wheels GPLv2** due to bundled codec libraries.
- The pillow-heif Windows wheel includes libheif (LGPLv3), libde265 (LGPLv3), x265 (GPLv2), and runtime support libraries. Distribution must include the applicable license texts, corresponding-source notices and GPL obligations; upstream list: https://github.com/bigcat88/pillow_heif/blob/master/LICENSES_bundled.txt
- PocketVideo is already licensed GPL-3.0-or-later; do not remove the third-party license/source obligations when distributing the Installer or portable bundle. Review HEVC patent licensing separately before distributing in additional jurisdictions.


## Distribution notice — REVIEW REQUIRED (2026-11-04)
The current installer combines GPL-3.0-or-later PocketVideo source with a
`pillow-heif==1.8.1` binary wheel. Upstream identifies wheel terms as GPLv2
(due to x265), libheif and libde265 as LGPLv3, and includes MinGW libraries.
Whether all of these exact binaries may be conveyed together depends on the
actual component license grants (including possible GPLv2-or-later choices),
corresponding-source offers, LGPL relinking rights, and local HEVC patents.
This document does **not** assert those rights have been cleared.
- Wheel attribution: https://github.com/bigcat88/pillow_heif/blob/v1.8.1/LICENSES_bundled.txt
- GPL license compatibility: https://www.gnu.org/licenses/gpl-faq.html#v2v3Compatibility
- See `docs/DISTRIBUTION_COMPLIANCE.md`. Do not tag a public release until reviewed.
