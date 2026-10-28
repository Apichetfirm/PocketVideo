# PocketVideo v1.0.0 Release Notes

## Highlights
- **Five Simple Compression Modes (Thai Default + English)**:
  - **Preset A — เน้นภาพสวย (Best Picture Quality)**: High-efficiency software CRF encoding preserving maximum visual detail.
  - **Preset B — คุ้มสุด (Best Balance, Default)**: Recommended for Android phone videos using H.265/HEVC (`hvc1` tagged MP4).
  - **Preset C — ประหยัดพื้นที่สุด (Maximum Space Saving)**: Smallest output files with automatic degradation warnings.
  - **Preset D — เร็วสุด (Fastest Compression)**: Automatic GPU hardware encoding (`NVENC`, `QSV`, `AMF`) with seamless CPU fallback.
  - **Preset E — กำหนดขนาดเอง (Custom Target File Size)**: Genuine two-pass software encoding (`libx264` / `libx265`) accounting for audio bitrate, subtitle tracks, and container muxing overhead.
- **Android Video Optimization**:
  - Preserves portrait/landscape rotation, 30/60fps & variable frame rate (`-fps_mode vfr`), 8-bit/10-bit pixel formats, and HDR10/HLG metadata.
  - Detects Dolby Vision and warns when proprietary RPU metadata cannot be guaranteed during re-encoding.
  - Detects already-efficiently-compressed inputs and warns before redundant recompression.
- **Smart Lossless Remux & Format Conversion**:
  - Converts between `MP4`, `MOV`, `MKV`, `AVI`, and `WebM`.
  - Automatically performs instant, zero-loss container remuxing (`-c copy`) when streams are compatible with the target container.
- **Enforceable Windows Resource Management**:
  - Enforces a genuine CPU hard cap (%) on child FFmpeg processes using Windows Job Objects (`JOBOBJECT_CPU_RATE_CONTROL_INFORMATION` with `JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP`), alongside process priority, thread count, and CPU affinity controls.
- **Before/After Visual Frame Comparison & Sample Preview**:
  - Generates a short sample compression clip and extracts aligned before/after frames so users can inspect visual detail before running a long encode.

## Checksums (`SHA256SUMS.txt`)
All Windows release artifacts (`PocketVideo-Setup-1.0.0-win64.exe` and `PocketVideo-1.0.0-win64-portable.zip`) are accompanied by `SHA256SUMS.txt` generated during the GitHub Actions release workflow.
