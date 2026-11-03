# Binary Distribution Readiness — manual signoff required

Status: **NOT APPROVED** as of 2026-11-04. This is a technical checklist, not legal advice.
PocketVideo v1.2.0 source can be merged and CI can test, but no public
GitHub Release should be tagged while distribution obligations are unresolved.

## Third-party binaries in Windows release
1. FFmpeg/FFprobe pinned BtbN build: verify exact flags, built version,
   x264/x265 codec terms, source availability and source-to-binary mapping.
2. PySide6/Qt 6: include LGPLv3 license notices and enable the user's rights
   to relink/replace the shared Qt DLLs in the PyInstaller bundle.
3. `pillow-heif==1.8.1`: wheel source license BSD-3-Clause; the project's
   bundled-license inventory explicitly lists binary wheel GPLv2, libheif
   LGPLv3, libde265 LGPLv3, and x265 GPLv2. **Verify** whether the exact x265
   component grants GPLv2-or-later and whether all combined code can be
   distributed under PocketVideo's GPL-3.0-or-later; do not assume compatibility.
4. Include actual license texts, copyright notices, corresponding source or
   legally sufficient source offers, and the build/installation information
   required under applicable GPL/LGPL grants.
5. Separately review patent and jurisdictional obligations for HEVC/H.265
   encoding/decoding; open-source copyright permission alone is not a patent license.
6. Install/uninstall on clean Windows 10/11 x64 and verify native codecs, 4K
   HEVC/HDR playback, CPU cap, image worker, and restart recovery on a physical
   USB/HDD test rig. Test power-loss and unplug scenarios with disposable media.
7. After authorized rights review and all quality checks, set repository
   variable `POCKETVIDEO_RELEASE_APPROVED=true` and tag the approved commit
   to allow the public GitHub Release workflow.

References:
- https://github.com/bigcat88/pillow_heif/blob/v1.8.1/LICENSES_bundled.txt
- https://www.gnu.org/licenses/gpl-faq.html#v2v3Compatibility
- https://www.x265.org/
- https://github.com/BtbN/FFmpeg-Builds
- https://doc.qt.io/qtforpython-6/licenses.html
