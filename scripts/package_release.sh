#!/bin/bash
# Package the generated apps and essential notices into a release zip.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.1.0-beta.1}"
DIST="$ROOT/dist"
STAGE="$ROOT/.build/Qwen-Scribe-$VERSION"
ARCHIVE="$DIST/Qwen-Scribe-$VERSION-macos-arm64.zip"

if [[ ! -d "$DIST/Qwen Scribe.app" || ! -d "$DIST/Stop Qwen Scribe.app" ]]; then
  echo "Run make app first." >&2
  exit 1
fi

rm -rf "$STAGE" "$ARCHIVE"
mkdir -p "$STAGE"
ditto "$DIST/Qwen Scribe.app" "$STAGE/Qwen Scribe.app"
ditto "$DIST/Stop Qwen Scribe.app" "$STAGE/Stop Qwen Scribe.app"
cp "$ROOT/README.md" "$ROOT/LICENSE" "$ROOT/PRIVACY.md" "$ROOT/THIRD_PARTY_NOTICES.md" "$STAGE/"
ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$ARCHIVE"

echo "Packaged $ARCHIVE"
