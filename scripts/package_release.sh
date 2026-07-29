#!/bin/bash
# Package the generated apps and essential notices into a release zip.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"

if [[ ! -d "$DIST/Qwen Scribe.app" || ! -d "$DIST/Stop Qwen Scribe.app" ]]; then
  echo "Run make app first." >&2
  exit 1
fi

# Name the archive after the bundle it actually contains, so a release zip can
# never claim a version the app inside it does not carry. RELEASE_TAG adds a
# pre-release suffix (0.1.0 -> 0.1.0-beta.1) that CFBundleShortVersionString
# itself must not contain.
BUNDLE_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
  "$DIST/Qwen Scribe.app/Contents/Info.plist")"
RELEASE_TAG="${RELEASE_TAG:-$BUNDLE_VERSION}"
if [[ "$RELEASE_TAG" != "$BUNDLE_VERSION" && "$RELEASE_TAG" != "$BUNDLE_VERSION-"* ]]; then
  echo "RELEASE_TAG '$RELEASE_TAG' does not match the built bundle's version $BUNDLE_VERSION." >&2
  exit 1
fi

STAGE="$ROOT/.build/Qwen-Scribe-$RELEASE_TAG"
ARCHIVE="$DIST/Qwen-Scribe-$RELEASE_TAG-macos-arm64.zip"

rm -rf "$STAGE" "$ARCHIVE"
mkdir -p "$STAGE"
ditto "$DIST/Qwen Scribe.app" "$STAGE/Qwen Scribe.app"
ditto "$DIST/Stop Qwen Scribe.app" "$STAGE/Stop Qwen Scribe.app"
# NOTICE ships too: Apache-2.0 section 4(d) requires it with every distribution.
for notice in README.md LICENSE NOTICE PRIVACY.md THIRD_PARTY_NOTICES.md; do
  cp "$ROOT/$notice" "$STAGE/$notice"
done
ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$ARCHIVE"

echo "Packaged $ARCHIVE"
