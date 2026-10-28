# PocketVideo Release Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair resource-control UI wiring, strengthen Windows-only verification, and prevent an untested Windows package from being represented as a verified release.

**Architecture:** Retain the existing Python/PySide6 application and FFmpeg subprocess backend. Bind UI settings directly to the existing `ResourceLimits` state, test the binding at the controller boundary and at the Qt widget boundary, and make Windows build validation and screenshots explicit CI gates.

**Tech Stack:** Python 3.12, PySide6, unittest, FFmpeg, Win32 Job Objects, PyInstaller, Inno Setup, GitHub Actions.

**Spec:** `README.md` and `docs/ARCHITECTURE.md`; user request in this conversation (distributable Windows `.exe`, bundled FFmpeg, controlled resources, GitHub release).

## Global Constraints

- Preserve source videos and all unrelated user files.
- Windows 10/11 x64 target; no claims of native verification from Linux tests.
- Keep Thai-default/English language switching and five presets.
- Do not expose a fake GPU-percentage cap or treat mocked Windows API tests as actual OS verification.
- Publish only after a Windows runner has produced and smoke-tested the real installer and portable package.

## Review Focus

- Changing resource controls via UI must affect the next generated encoding job.
- Existing queued jobs must not be retroactively mutated by UI settings changes.
- Windows Job Object handle operations must be pointer-width-safe on x64.
- Native Qt screenshots must be distinguished from HTML design previews.
- GitHub release must be gated on actual Windows packaging success.

---

### Task 1: Wire resource settings to controller state

**Files:** `src/pocketvideo/ui/main_window.py`, `src/pocketvideo/ui/controller.py`, `tests/test_pocketvideo.py`, `tests/test_ui_resource_controls.py`.

**Interfaces:** `AppController.resource_limits: ResourceLimits` and the existing Qt widgets `threads_spin`, `concurrent_spin`, `chk_gpu_enable`, `cpu_cap_spin`.

- [ ] Add a failing controller-level test: changing resource settings updates the values used by a newly enqueued job, persists them, and leaves an already queued job unchanged.
- [ ] Run `PYTHONPATH=src python -m unittest tests/test_pocketvideo.py -v` and confirm the new behavior fails.
- [ ] Add a minimal controller method for validated updates to `ResourceLimits` and connect all four UI controls to it; avoid recursive value-change loops when resource modes set widget values.
- [ ] Add a Qt offscreen integration test (skip only when PySide6 is unavailable) changing widget values and asserting the resulting controller state.
- [ ] Run the complete suite: `PYTHONPATH=src python -m unittest discover -s tests -v` (expect all executable tests pass).

### Task 2: Verify resource-cap API behavior on real Windows

**Files:** `src/pocketvideo/resources/job_object.py`, `tests/test_windows_integration.py`.

**Interfaces:** `ProcessResourceController.attach_to_process` and `close`; `ResourceLimits.cpu_hard_cap_percent`.

- [ ] Add a Windows-only integration test using a harmless child process to exercise actual Win32 handle creation, configuration, assignment, and cleanup; skip on non-Windows.
- [ ] Confirm failure on Windows if the current handle/API setup cannot satisfy it; check Win32 errors rather than assuming success.
- [ ] Declare Win32 pointer-width-safe ctypes `argtypes`/`restype` for handle APIs; ensure assignment failure or handle-open failure clears the hard-cap status and closes handles.
- [ ] Run the full test suite locally and require the Windows integration test in CI.

### Task 3: Make Windows release verifiable and documentation honest

**Files:** `.github/workflows/windows-build-release.yml`, `packaging/build_windows.ps1`, `README.md`, `scripts/capture_screenshots.py`, `tests/test_packaging.py`.

- [ ] Add a failing behavioral test around packaging dependency discovery and truthful screenshot generation/fallback (do not assert only on source-code strings).
- [ ] Resolve the Inno Setup compiler using PATH or standard installation locations, fail explicitly if a required installer cannot be built; verify final installer/portable file existence and nonzero sizes.
- [ ] Distinguish HTML concept previews from actual Qt screenshots; capture/upload real Qt screenshots on Windows CI after application initialization.
- [ ] Confirm workflow release occurs only after build and executable smoke tests; document bundled FFmpeg verification requirements and update placeholder GitHub links after repository identity is known.
- [ ] Run tests and review the workflow without claiming a Windows build has succeeded before Actions runs.

### Task 4: Deliver source and prepare GitHub publication

**Files:** all changed source, tests, documentation, release packaging.

- [ ] Run all 13 existing tests plus new tests, compile/import checks, and inspect changed files.
- [ ] Create a new source ZIP and preserve the original upload unchanged.
- [ ] If a suitable `Apichetfirm/PocketVideo` GitHub repository exists and write permissions are available, publish only the verified source with clear pre-release status; otherwise ask for a blank repository URL/approval to create it.
- [ ] Trigger or request the Windows CI workflow, inspect results, and only then tag/publish a public v1.0.0 release. Never claim `.exe` completion without the actual Windows runner artifact.
