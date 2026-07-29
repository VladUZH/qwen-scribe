#!/bin/bash
# Create or update a relocatable project development environment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# run.sh and the Makefile both hardcode .venv, so this location is not an
# override point; PYTHON_BIN is the one knob that works everywhere.
VENV_DIR="$ROOT/.venv"
REQUIREMENTS_FILE="$ROOT/requirements-lock.txt"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MARKER="$VENV_DIR/.qwen-scribe-requirements.sha256"

# numpy 2.5.1 in requirements-lock.txt requires Python >= 3.12.
MIN_PYTHON_VERSION="3.12"
MIN_PYTHON_MINOR="12"

if [[ -f "$VENV_DIR/pyvenv.cfg" ]]; then
  VERSION="$(sed -n 's/^version = \([0-9][0-9.]*\).*/\1/p' "$VENV_DIR/pyvenv.cfg" | head -1)"
  MAJOR="${VERSION%%.*}"
  REST="${VERSION#*.}"
  MINOR="${REST%%.*}"
  if [[ -n "$VERSION" ]] && { [[ "${MAJOR:-0}" -lt 3 ]] || { [[ "${MAJOR:-0}" -eq 3 ]] && [[ "${MINOR:-0}" -lt "$MIN_PYTHON_MINOR" ]]; }; }; then
    BACKUP="${VENV_DIR}-python-${VERSION}-$(date +%Y%m%d-%H%M%S)"
    echo "Moving incompatible Python $VERSION environment to $BACKUP"
    mv "$VENV_DIR" "$BACKUP"
  fi
fi

# A Homebrew Python upgrade leaves bin/python dangling. Recreating over the
# top would keep the old lib/pythonX.Y tree and the requirements marker, so
# the install below would be skipped and the environment left empty.
if [[ -e "$VENV_DIR" ]] && ! "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
  BROKEN="${VENV_DIR}-broken-$(date +%Y%m%d-%H%M%S)"
  echo "Moving unusable Python environment to $BROKEN"
  mv "$VENV_DIR" "$BROKEN"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Python $MIN_PYTHON_VERSION or newer is required." >&2
    exit 1
  fi
  if ! "$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, $MIN_PYTHON_MINOR) else 1)"; then
    echo "Python $MIN_PYTHON_VERSION or newer is required (found $("$PYTHON_BIN" -V 2>&1))." >&2
    exit 1
  fi
  echo "Creating Python environment at $VENV_DIR"
  rm -rf "$VENV_DIR"
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
