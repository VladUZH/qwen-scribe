#!/bin/bash
# Create or update a relocatable project development environment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV_DIR="${VENV_DIR:-$ROOT/.venv}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-$ROOT/requirements-lock.txt}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MARKER="$VENV_DIR/.qwen-scribe-requirements.sha256"

if [[ -f "$VENV_DIR/pyvenv.cfg" ]]; then
  VERSION="$(sed -n 's/^version = \([0-9][0-9.]*\).*/\1/p' "$VENV_DIR/pyvenv.cfg" | head -1)"
  MAJOR="${VERSION%%.*}"
  REST="${VERSION#*.}"
  MINOR="${REST%%.*}"
  if [[ -n "$VERSION" ]] && { [[ "${MAJOR:-0}" -lt 3 ]] || { [[ "${MAJOR:-0}" -eq 3 ]] && [[ "${MINOR:-0}" -lt 10 ]]; }; }; then
    BACKUP="${VENV_DIR}-python-${VERSION}-$(date +%Y%m%d-%H%M%S)"
    echo "Moving incompatible Python $VERSION environment to $BACKUP"
    mv "$VENV_DIR" "$BACKUP"
  fi
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Python 3.10 or newer is required." >&2
    exit 1
  fi
  if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Python 3.10 or newer is required." >&2
    exit 1
  fi
  echo "Creating Python environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade pip
fi

REQ_HASH="$(/usr/bin/shasum -a 256 "$REQUIREMENTS_FILE" | awk '{print $1}')"
INSTALLED_HASH="$(cat "$MARKER" 2>/dev/null || true)"
if [[ "$REQ_HASH" != "$INSTALLED_HASH" ]]; then
  echo "Installing pinned Python dependencies"
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet -r "$REQUIREMENTS_FILE"
  printf '%s\n' "$REQ_HASH" > "$MARKER"
fi
