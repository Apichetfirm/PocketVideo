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
