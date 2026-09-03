#!/bin/bash
# Build relocatable, ad-hoc-signed macOS app bundles from tracked sources.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/release_versions.sh"
DIST="${DIST_DIR:-$ROOT/dist}"
VERSION="${VERSION:-0.2.2}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
IDENTITY="${CODESIGN_IDENTITY:--}"
APP="$DIST/Qwen Scribe.app"
STOP_APP="$DIST/Stop Qwen Scribe.app"

if ! qs_valid_bundle_version "$VERSION"; then
  echo "VERSION must be a three-part version such as 0.2.0 (got '$VERSION')." >&2
  exit 1
fi
if [[ ! "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
  echo "BUILD_NUMBER must be a positive integer (got '$BUILD_NUMBER')." >&2
  exit 1
fi

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

cp "$ROOT/macos/launcher.sh" "$APP/Contents/Resources/launch-server.sh"
cp "$ROOT/macos/QwenScribe-Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cp "$ROOT/server.py" "$ROOT/requirements.txt" "$ROOT/requirements-lock.txt" "$APP/Contents/Resources/"
cp -R "$ROOT/static/." "$APP/Contents/Resources/static/"
cp -R "$ROOT/qwen_scribe" "$APP/Contents/Resources/qwen_scribe"
# Bytecode from a developer checkout must not ship inside the bundle.
find "$APP/Contents/Resources/qwen_scribe" -name __pycache__ -type d -prune -exec rm -rf {} +

cp "$ROOT/macos/stop.sh" "$STOP_APP/Contents/MacOS/StopQwenScribe"
cp "$ROOT/macos/StopQwenScribe-Info.plist" "$STOP_APP/Contents/Info.plist"

chmod +x "$APP/Contents/Resources/launch-server.sh" "$STOP_APP/Contents/MacOS/StopQwenScribe"

clang -fobjc-arc -arch arm64 -mmacosx-version-min=14.0 -Wall -Wextra \
  -Wno-unused-parameter \
  -framework Cocoa -framework ApplicationServices -framework AVFoundation \
  -framework AudioToolbox \
  "$ROOT/native/DictationHelper.m" -o "$APP/Contents/MacOS/QwenScribe"

for plist in "$APP/Contents/Info.plist" "$STOP_APP/Contents/Info.plist"; do
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$plist"
  plutil -lint "$plist" >/dev/null
done

if [[ "$IDENTITY" == "-" ]]; then
  codesign --force --sign - "$APP"
  codesign --force --sign - "$STOP_APP"
else
  # The hardened runtime blocks microphone capture without this entitlement,
  # so a signed release without it ships with dictation silently broken.
  codesign --force --options runtime --timestamp \
    --entitlements "$ROOT/macos/QwenScribe.entitlements" --sign "$IDENTITY" "$APP"
  codesign --force --options runtime --timestamp --sign "$IDENTITY" "$STOP_APP"

  # Assert the entitlement actually survived signing — the failure mode it
  # guards against is silent (no prompt, no error, dictation just records
  # nothing). The dots in the key must be escaped: plutil treats unescaped
  # dots as key-path separators and would report the entitlement missing
  # from a perfectly good build.
  EMBEDDED="$(codesign -d --entitlements - --xml "$APP" 2>/dev/null || true)"
  if [[ -z "$EMBEDDED" ]]; then
    echo "Signed build carries no entitlements — dictation would lose the microphone." >&2
    exit 1
  fi
  if [[ "$(printf '%s' "$EMBEDDED" | plutil -extract 'com\.apple\.security\.device\.audio-input' raw - 2>/dev/null)" != "true" ]]; then
    echo "The audio-input entitlement is missing from the signed build." >&2
    exit 1
  fi
fi

codesign --verify --deep --strict "$APP"
codesign --verify --strict "$STOP_APP"

MAIN_EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
if [[ "$MAIN_EXECUTABLE" != "QwenScribe" ]] || ! file "$APP/Contents/MacOS/$MAIN_EXECUTABLE" | grep -q 'Mach-O 64-bit executable arm64'; then
  echo "The privacy-sensitive native process is not the arm64 bundle executable." >&2
  exit 1
fi
if [[ -e "$APP/Contents/MacOS/QwenScribeDictation" ]]; then
  echo "Unexpected legacy nested dictation executable." >&2
  exit 1
fi

echo "Built:"
echo "  $APP"
echo "  $STOP_APP"
