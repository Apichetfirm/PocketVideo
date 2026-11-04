# PocketVideo Release Notes

## v1.3.2 — CPU Control & Shareable Savings (development)
- 10–100% Windows CPU hard-cap menu with one-percent Custom value and
  persistent selection; explanatory FFmpeg Threads help (0=Auto).
- History metadata is independent of current source/output files and skips
  corrupt rows individually. Saved lifetime totals are stored separately,
  count verified completed jobs once and survive history pruning.
- Weighted net saved bytes, percentage, file count, branded clipboard image
  and social-copy text with the Thai quote
  “พื้นที่มากขึ้น ชีวิตก็มีความเป็นไปได้มากขึ้น”.
- Savings represent compression output-size differences, not guaranteed
  currently reclaimed filesystem space while originals remain.


## v1.3.1 — Developer Build
Original brand/app icons and platform-style buttons, clean menu actions,
branded Windows installer and macOS Dock icon. Expanded macOS CI to Apple
Silicon and Intel with packaged compression smoke tests. Gated, opt-in
Developer ID signing/notarization workflow included for authorized
distribution; nothing is automatically signed or published.

# PocketVideo Release Notes

## v1.3.0 — Development (not public Release)
Queue customization, persisted column sizes and order, timestamps, readable results,
preprocessing source/output folder shortcuts, compact responsive layout, per-job retry,
and GPU backend preferences (NVENC, QSV, AMF, VideoToolbox when available).
macOS source and .app CI alpha started. No notarized or released macOS distribution.

# PocketVideo Release Notes

## v1.2.0 — Development (not a public release)

Reliability improvements: queue recovery journal (manual resume), proactive
external-drive checks, opt-in skip of larger compressed results, Windows
Job Object resource caps on image worker subprocesses, nonblocking import,
optional bounded SSIM/VMAF analysis, and queue action cleanup.

Distribution of native HEIC codec wheels is subject to manual third-party
license/source/patent review; see `docs/DISTRIBUTION_COMPLIANCE.md`.

## Existing public release

v1.0.0 remains the last public GitHub Release until the release process is
approved. Please do not equate a passed CI build with a license clearance.
