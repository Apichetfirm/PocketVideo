# PocketVideo Technical Architecture

## 1. Architectural Overview
PocketVideo follows a strict layered architecture with zero circular dependencies and clear separation between UI presentation, media inspection/encoding, resource control, queue orchestration, and persistence:

```text
PocketVideo/
├── src/pocketvideo/
│   ├── core/
│   │   ├── models.py       # Immutable & typed dataclasses/enums for streams, jobs, plans, and results
│   │   ├── i18n.py         # Bilingual localization engine (Thai default + English) & formatters
│   │   └── config.py       # Atomic JSON settings & history persistence + rotating file logging
│   ├── media/
│   │   ├── binaries.py     # Bundled/custom/PATH FFmpeg & FFprobe discovery, validation & GPU probing
│   │   ├── probe.py        # FFprobe JSON media analyzer (HDR10/HLG/Dolby Vision, VFR, orientation, bpp)
│   │   ├── presets.py      # Presets A-E definitions, 2-pass target bitrate math, Smart Remux rules
│   │   └── encoder.py      # Safe subprocess argument builder, -progress pipe:1 parser, sample previewer
│   ├── resources/
│   │   └── job_object.py   # Win32 Job Object CPU Hard Cap (ctypes.windll.kernel32), priority, suspend/resume
│   ├── queue/
│   │   └── manager.py      # Thread-safe batch dispatcher, temp output staging, post-encode integrity check
│   ├── ui/
│   │   ├── theme.py        # Cohesive Dark & Light QSS design system
│   │   ├── controller.py   # Headless-testable Presentation Controller / ViewModel
│   │   └── main_window.py  # PySide6 / Qt 6 desktop interface with thread-safe Signal marshaling
│   └── main.py             # GUI & headless CLI entry point
├── packaging/
│   ├── fetch_ffmpeg_windows.py  # Pinned URL + SHA-256 verification + --enable-nonfree guard
│   ├── pocketvideo.spec         # Reproducible PyInstaller x64 bundle specification
│   ├── installer.iss            # Inno Setup 6 x64 installer script
│   └── build_windows.ps1        # End-to-end Windows build, smoke test & SHA256SUMS generator
└── tests/
    ├── test_pocketvideo.py      # 11 integration/unit test suites against real FFmpeg/FFprobe
    └── test_packaging.py        # Dedicated packaging, SHA-256 checksum & spec verification tests
```

## 2. Two-Pass Target Size Calculation (Preset E)
When the user specifies a target output size $S_{\text{target}}$ (in MB or GB), `calculate_target_size_plan()` computes:
1. **Container & Muxing Reserve**: $B_{\text{overhead}} = \max(65536,\; 0.02 \times B_{\text{target}})$ plus subtitle reserve ($1024\text{ bytes/sec/track}$).
2. **Audio Stream Budget**: Inspects all input audio streams via `ffprobe`. If copying the existing audio uses $\le 20\%$ of the total bitrate budget, stream copy (`-c:a copy`) is preserved; otherwise audio is stepped down cleanly (`128k`, `96k`, or `64k` per stream).
3. **Target Video Bitrate**:
   $$R_{\text{video}} = \frac{8 \times (B_{\text{target}} - B_{\text{overhead}} - B_{\text{sub}})}{T_{\text{duration}}} - R_{\text{audio}}$$
4. **Feasibility & Quality Risk Assessment**: Rejects impossible targets ($R_{\text{video}} < 45\text{ kbps}$) before spawning FFmpeg, and calculates bits-per-pixel-per-frame ($\text{bpp}$) to warn when moderate or high visual degradation is likely.
5. **Genuine Two-Pass Execution**: Executes Pass 1 writing rate-control statistics to a job-scoped temporary log file (`-pass 1 -passlogfile` for `libx264`/`libvpx-vp9` or `-x265-params pass=1:stats=...` for `libx265`) with null output, followed by Pass 2 producing the temporary media file, and cleans up all pass logs in a `finally` block.

## 3. Windows Job Object Resource Enforcement
On Windows 10/11 x64, `ProcessResourceController` in `resources/job_object.py` creates a Win32 Job Object for each child `ffmpeg.exe` process:
- Sets `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so orphaned FFmpeg processes can never survive an unexpected termination.
- Sets `JobObjectCpuRateControlInformation` with `JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP` and `CpuRate = cpu_hard_cap_percent * 100`, enforcing a genuine kernel-level CPU cap on `ffmpeg.exe`.
- Uses `NtSuspendProcess` / `NtResumeProcess` on Windows (and `SIGSTOP` / `SIGCONT` on POSIX) for reliable job pause and resume.
