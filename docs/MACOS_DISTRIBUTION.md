# PocketVideo macOS Distribution Checklist

**State: NOT RELEASE APPROVED.** This guide wires the *future* Developer ID
signing/notarization path; it does not imply that an Apple ticket exists.

## What is already automated
- Windows and macOS builds use an original PocketVideo app/installer icon.
- macOS-15 Apple Silicon (arm64) and macos-15-intel run separate test, build,
  signature-verification, and bundled FFmpeg compression smoke jobs.
- Signed/notarized distribution is manually invoked from the dedicated
  `macOS Developer ID Distribution` workflow, never PR or push builds.
- The workflow cannot run unless **both** repository variables are explicitly
  set to `true`: `POCKETVIDEO_RELEASE_APPROVED` and
  `MACOS_DISTRIBUTION_APPROVED`.

## Required Apple credentials (repository Actions secrets)
Add the values in GitHub Settings > Secrets and variables > Actions. Do not
commit certificate files or private keys to the repository.

| Secret | Description |
| --- | --- |
| `MACOS_CERTIFICATE_P12_BASE64` | Exported Developer ID Application .p12 encoded as base64 |
| `MACOS_CERTIFICATE_PASSWORD` | Password for .p12 |
| `MACOS_KEYCHAIN_PASSWORD` | Strong temporary CI keychain password |
| `MACOS_SIGNING_IDENTITY` | Exact `Developer ID Application: ... (TEAMID)` certificate name |
| `MACOS_APPLE_ID` | Apple ID used for notarization |
| `MACOS_APP_SPECIFIC_PASSWORD` | Apple app-specific password for notarytool |
| `MACOS_TEAM_ID` | Apple Developer team ID |

The owner must have access to an Apple Developer Program account and Developer
ID certificate. On a secure Mac, export the approved certificate as a .p12
after verifying developer-account authority. Upload via GitHub Secrets.
CI imports it into a temporary keychain, enables Hardened Runtime and secure
timestamp, calls `xcrun notarytool submit --wait`, validates an **Accepted**
response, staples and validates the ticket, then creates signed .zip and .dmg.
No credentials in source, logs, or build artifacts.

## Before enabling the release variables
1. Complete `docs/DISTRIBUTION_COMPLIANCE.md` for exact shipped FFmpeg,
   Qt, LGPL/GPL, x265/libheif rights, corresponding source and patents. Have
   an authorized person record the legal approval.
2. Test the specific arm64 and Intel macOS apps on physical Macs with clean
   user environments (not only Homebrew development machines). Include
   HEVC/HDR/4K, Qt Multimedia, HEIC, USB disconnect, cancellation/retry,
   large batch, and source-backup overwrite/recovery.
3. Verify any Homebrew-linked FFmpeg dylibs are packaged inside the bundle.
   Investigate external library dependencies from `otool -L`; CI success on
   a Homebrew runner alone is **not proof of standalone portability**.
4. Set the two approval variables, configure all seven Apple secrets, then
   run `macOS Developer ID Distribution` from `main` manually.
5. Check Apple notary log and Gatekeeper assessment; only release successful,
   notarized/stapled artifacts for the verified architecture. Public GitHub
   Release publication remains a separate deliberate step.

References:
- https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- https://developer.apple.com/help/account/certificates/create-developer-id-certificates/


## Standalone FFmpeg portability check
The macOS alpha workflow runs `packaging/check_macos_dependencies.py` against
the actual `.app` to reject dependencies with absolute Homebrew paths.
It also launches the bundled app with Homebrew removed from `PATH`, then
performs a full packaged compression. Passing both reduces (but does not
eliminate) clean-machine compatibility risks.


## Qt-vs-Homebrew FFmpeg ABI separation
The macOS app bundles Qt Multimedia and a separate FFmpeg command-line encoder.
Their `libavcodec` libraries may share a Mach-O install name but not ABI.
Builds must run `dylibbundler` on **copied binaries in the bundle only**, not on
Homebrew's source binaries, to isolate Homebrew FFmpeg dylibs in
`Contents/Frameworks/ffmpeg_libs`. Never resolve FFmpeg CLI's private
`libavcodec` via `PySide6/Qt/lib`. Both arm64 and Intel CI must pass a
real `ffmpeg -version` and encoded output smoke test from the packaged app.
