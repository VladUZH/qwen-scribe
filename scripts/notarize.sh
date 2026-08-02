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
ARCHIVE_DIR="$(cd "$(dirname "$ARCHIVE")" && pwd)"
ARCHIVE="$ARCHIVE_DIR/$(basename "$ARCHIVE")"
REPACK_DIR="$(mktemp -d "$ARCHIVE_DIR/.qwen-scribe-repack.XXXXXX")"
REPACKED="$REPACK_DIR/$(basename "$ARCHIVE")"
trap 'rm -rf "$WORK" "$REPACK_DIR"' EXIT

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

echo "Verifying the stapled apps…"
xcrun stapler validate "$STAGE/Qwen Scribe.app"
xcrun stapler validate "$STAGE/Stop Qwen Scribe.app"
spctl --assess --type execute --verbose "$STAGE/Qwen Scribe.app"
spctl --assess --type execute --verbose "$STAGE/Stop Qwen Scribe.app"

echo "Re-packing the stapled archive…"
# Keep the submitted archive intact until its stapled replacement has been
# created successfully on the same filesystem, then replace it atomically.
ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$REPACKED"
/usr/bin/unzip -tq "$REPACKED"
mv -f "$REPACKED" "$ARCHIVE"

# Re-packing changed the archive's hash. Regenerate the checksum file here so
# a stale published SHA256SUMS.txt can never make a legitimate stapled build
# look tampered with — forgetting this step manually was the failure mode.
SUMS="$(dirname "$ARCHIVE")/SHA256SUMS.txt"
ARCHIVE_NAME="$(basename "$ARCHIVE")"
NEW_LINE="$(cd "$(dirname "$ARCHIVE")" && shasum -a 256 "$ARCHIVE_NAME")"
if [[ -f "$SUMS" ]]; then
  grep -vF "  $ARCHIVE_NAME" "$SUMS" > "$SUMS.tmp" || true
  printf '%s\n' "$NEW_LINE" >> "$SUMS.tmp"
  mv "$SUMS.tmp" "$SUMS"
else
  printf '%s\n' "$NEW_LINE" > "$SUMS"
fi

echo
echo "Done. $SUMS now matches the stapled archive."
echo "Re-upload BOTH files to the release together:"
echo "  gh release upload <tag> --clobber \"$ARCHIVE\" \"$SUMS\""
