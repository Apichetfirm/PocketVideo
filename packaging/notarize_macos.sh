#!/usr/bin/env bash
# Developer ID signing + Apple notarization. ONLY invoke after compliance signoff.
set -euo pipefail
cd "$(dirname "$0")/.."

required=(
  MACOS_CERTIFICATE_P12_BASE64 MACOS_CERTIFICATE_PASSWORD
  MACOS_KEYCHAIN_PASSWORD MACOS_SIGNING_IDENTITY
  MACOS_APPLE_ID MACOS_APP_SPECIFIC_PASSWORD MACOS_TEAM_ID
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Required signing/notarization secret is missing: $name" >&2
    exit 2
  fi
done
if [[ "${POCKETVIDEO_RELEASE_APPROVED:-}" != "true" ||
      "${MACOS_DISTRIBUTION_APPROVED:-}" != "true" ]]; then
  echo "Legal and macOS distribution approval gates must BOTH be true." >&2
  exit 2
fi
APP="dist/PocketVideo.app"
if [[ ! -d "$APP" ]]; then
  echo "Missing compiled macOS .app; run packaging/build_macos.sh first" >&2
  exit 1
fi
WORK="$(mktemp -d "${RUNNER_TEMP:-/tmp}/pocketvideo-notary.XXXXXX")"
KEYCHAIN="$WORK/codesign.keychain-db"
P12="$WORK/developer-id.p12"
cleanup() {
  security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
umask 077
printf "%s" "$MACOS_CERTIFICATE_P12_BASE64" | base64 -D > "$P12"
security create-keychain -p "$MACOS_KEYCHAIN_PASSWORD" "$KEYCHAIN"
security set-keychain-settings -lut 21600 "$KEYCHAIN"
security unlock-keychain -p "$MACOS_KEYCHAIN_PASSWORD" "$KEYCHAIN"
security import "$P12" -k "$KEYCHAIN" \
  -P "$MACOS_CERTIFICATE_PASSWORD" -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple: -s \
  -k "$MACOS_KEYCHAIN_PASSWORD" "$KEYCHAIN"
security list-keychains -d user -s "$KEYCHAIN"
# Hardened Runtime and secure timestamp are mandatory for modern notarization.
codesign --force --deep --options runtime --timestamp \
  --keychain "$KEYCHAIN" --sign "$MACOS_SIGNING_IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv --verbose=4 "$APP" 2>&1 | grep -E "Authority=|TeamIdentifier=|Runtime" || true

mkdir -p dist/macos-signed
ARCH="$(uname -m)"
SUBMISSION="$WORK/PocketVideo-notarization.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$SUBMISSION"
xcrun notarytool submit "$SUBMISSION" \
  --apple-id "$MACOS_APPLE_ID" \
  --password "$MACOS_APP_SPECIFIC_PASSWORD" \
  --team-id "$MACOS_TEAM_ID" \
  --wait --timeout 30m --output-format json > "$WORK/notarization.json"
python - "$WORK/notarization.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    result = json.load(handle)
status = result.get("status")
print("Apple notarization status:", status)
if status != "Accepted":
    raise SystemExit("Apple did not accept notarization; do not distribute.")
PY
xcrun stapler staple --verbose "$APP"
xcrun stapler validate --verbose "$APP"
spctl --assess --type execute --verbose=4 "$APP"

ZIP="dist/macos-signed/PocketVideo-1.3.4-macos-${ARCH}-notarized.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
DMG="dist/macos-signed/PocketVideo-1.3.4-macos-${ARCH}-notarized.dmg"
hdiutil create -ov -format UDZO -volname PocketVideo \
  -srcfolder "$APP" "$DMG"
shasum -a 256 "$ZIP" "$DMG" > dist/macos-signed/SHA256SUMS.txt
echo "Developer ID signed + Apple notarized, stapled, and verified: $ZIP, $DMG"
