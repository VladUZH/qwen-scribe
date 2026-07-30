#!/bin/bash
# Notarize and staple a packaged Qwen Scribe release (release-owner step).
#
# Requires a Developer ID-signed build (CODESIGN_IDENTITY at make app time)
# and stored notarytool credentials:
#   xcrun notarytool store-credentials qwen-scribe \
#     --apple-id <apple-id> --team-id <team-id> --password <app-specific>
#
# Usage: scripts/notarize.sh dist/Qwen-Scribe-<version>-macos-arm64.zip
set -euo pipefail

PROFILE="${NOTARY_PROFILE:-qwen-scribe}"
ARCHIVE="${1:?usage: scripts/notarize.sh <release zip>}"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "No such archive: $ARCHIVE" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Submitting $ARCHIVE for notarization (profile: $PROFILE)…"
xcrun notarytool submit "$ARCHIVE" --keychain-profile "$PROFILE" --wait

echo "Stapling tickets into the app bundles…"
ditto -x -k "$ARCHIVE" "$WORK"
STAGE="$(find "$WORK" -maxdepth 1 -type d -name 'Qwen-Scribe-*' | head -1)"
if [[ -z "$STAGE" ]]; then
  echo "The archive does not contain the expected Qwen-Scribe-<version> folder." >&2
  exit 1
fi
xcrun stapler staple "$STAGE/Qwen Scribe.app"
xcrun stapler staple "$STAGE/Stop Qwen Scribe.app"

echo "Re-packing the stapled archive…"
rm -f "$ARCHIVE"
ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$ARCHIVE"

echo "Verifying…"
spctl --assess --type execute --verbose "$STAGE/Qwen Scribe.app"

echo
echo "Done. Recompute the checksum before uploading:"
echo "  shasum -a 256 $ARCHIVE"
