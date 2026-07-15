#!/bin/bash
# Build relocatable, ad-hoc-signed macOS app bundles from tracked sources.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="${DIST_DIR:-$ROOT/dist}"
VERSION="${VERSION:-0.1.0}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
IDENTITY="${CODESIGN_IDENTITY:--}"
APP="$DIST/Qwen Scribe.app"
STOP_APP="$DIST/Stop Qwen Scribe.app"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS app bundles must be built on macOS." >&2
  exit 1
fi
if ! command -v clang >/dev/null 2>&1; then
  echo "Apple Command Line Tools are required (xcode-select --install)." >&2
  exit 1
fi

rm -rf "$APP" "$STOP_APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/static" "$STOP_APP/Contents/MacOS"

cp "$ROOT/macos/launcher.sh" "$APP/Contents/MacOS/QwenScribe"
cp "$ROOT/macos/QwenScribe-Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/server.py" "$ROOT/requirements.txt" "$ROOT/requirements-lock.txt" "$APP/Contents/Resources/"
cp -R "$ROOT/static/." "$APP/Contents/Resources/static/"

cp "$ROOT/macos/stop.sh" "$STOP_APP/Contents/MacOS/StopQwenScribe"
cp "$ROOT/macos/StopQwenScribe-Info.plist" "$STOP_APP/Contents/Info.plist"

chmod +x "$APP/Contents/MacOS/QwenScribe" "$STOP_APP/Contents/MacOS/StopQwenScribe"

clang -fobjc-arc -arch arm64 -mmacosx-version-min=14.0 -Wall -Wextra \
  -Wno-unused-parameter \
  -framework Cocoa -framework ApplicationServices -framework AVFoundation \
  -framework AudioToolbox \
  "$ROOT/native/DictationHelper.m" -o "$APP/Contents/MacOS/QwenScribeDictation"

for plist in "$APP/Contents/Info.plist" "$STOP_APP/Contents/Info.plist"; do
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$plist"
  plutil -lint "$plist" >/dev/null
done

if [[ "$IDENTITY" == "-" ]]; then
  codesign --force --deep --sign - "$APP"
  codesign --force --sign - "$STOP_APP"
else
  codesign --force --deep --options runtime --timestamp --sign "$IDENTITY" "$APP"
  codesign --force --options runtime --timestamp --sign "$IDENTITY" "$STOP_APP"
fi

codesign --verify --deep --strict "$APP"
codesign --verify --strict "$STOP_APP"

echo "Built:"
echo "  $APP"
echo "  $STOP_APP"
