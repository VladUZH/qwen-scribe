#!/bin/bash
# Qwen Scribe — reproducible setup and launch for Apple Silicon Macs.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Qwen Scribe requires an Apple Silicon Mac." >&2
  exit 1
fi

MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [[ "$MACOS_MAJOR" -lt 14 ]]; then
  echo "Qwen Scribe requires macOS 14 or newer (MLX requirement)." >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Warning: ffmpeg is missing. WAV files still work, but other audio and video need it."
  echo "Install it with: brew install ffmpeg"
fi

# The only routine network activity should be explicit dependency/model
# downloads. Disable optional Hugging Face telemetry in the server process.
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1

./scripts/setup_python.sh

echo
echo "Starting Qwen Scribe on http://127.0.0.1:${QWEN_SCRIBE_PORT:-8990}"
echo "The first transcription downloads the selected model once."
echo
exec .venv/bin/python server.py
