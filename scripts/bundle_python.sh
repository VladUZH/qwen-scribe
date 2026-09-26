#!/bin/bash
# Install the Python runtime the app ships with, so a Mac needs no Python of
# its own. Fetches a python-build-standalone `install_only` build for Apple
# Silicon, verifies it against the hash pinned below, unpacks it into the
# destination, and removes what a transcription app will never load.
#
#   scripts/bundle_python.sh <destination directory>
#
# The archive is cached under .build/cache, so a second `make app` is offline.
set -euo pipefail

# Pinned by hash rather than by tag: this is the interpreter that will run the
# server on someone else's Mac, so it is reviewed like any other dependency.
# The hash is the one published in the release's SHA256SUMS file, and any
# mismatch stops the build rather than shipping an unexpected runtime.
PYTHON_VERSION="3.12.14"
PYTHON_RELEASE="20260901"
PYTHON_ARCHIVE="cpython-${PYTHON_VERSION}+${PYTHON_RELEASE}-aarch64-apple-darwin-install_only.tar.gz"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}/${PYTHON_ARCHIVE}"
PYTHON_SHA256="3ee3ee547cedfeb7c2b16b2b7156039f7b470bb8f857e226fd3d2eb11db83c76"

DEST="${1:-}"
if [[ -z "$DEST" ]]; then
  echo "usage: $(basename "$0") <destination directory>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${QS_BUILD_CACHE:-$ROOT/.build/cache}"
ARCHIVE="$CACHE/$PYTHON_ARCHIVE"

verified() {
  [[ -f "$1" ]] && printf '%s  %s\n' "$PYTHON_SHA256" "$1" | shasum -a 256 -c --status -
}

mkdir -p "$CACHE"
if ! verified "$ARCHIVE"; then
  echo "Fetching the bundled Python runtime ($PYTHON_VERSION)…"
  curl -fSL --retry 3 --retry-delay 2 "$PYTHON_URL" -o "$ARCHIVE.part"
  if ! verified "$ARCHIVE.part"; then
    rm -f "$ARCHIVE.part"
    echo "The Python runtime archive does not match its pinned SHA-256. Refusing to bundle it." >&2
    exit 1
  fi
  mv "$ARCHIVE.part" "$ARCHIVE"
fi

rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
tar -xzf "$ARCHIVE" -C "$STAGE"
mv "$STAGE/python" "$DEST"

# Tcl/Tk and the tools built on it: a menu-bar app with a web interface has no
# use for them, and they are the largest thing in the archive after the
# interpreter itself. Nothing else is removed — a stdlib with holes in it is a
# support problem waiting to happen, and pip needs ensurepip to make the
# private environment.
rm -rf \
  "$DEST/lib/python3.12/idlelib" \
  "$DEST/lib/python3.12/turtledemo" \
  "$DEST/lib/python3.12/tkinter" \
  "$DEST/lib/python3.12/lib-dynload/_tkinter."*.so \
  "$DEST/lib/libtcl"* "$DEST/lib/libtk"* \
  "$DEST/lib/tcl"* "$DEST/lib/tk"* "$DEST/lib/itcl"* "$DEST/lib/thread"* \
  "$DEST/share/man" \
  "$DEST/bin/idle"* "$DEST/bin/2to3"*

# Console scripts nothing here calls: the launcher runs `python -m venv` and
# then `python -m pip`, and the environment it makes gets its own pip from
# the ensurepip wheel. Removing them keeps a handful of shebang scripts out
# of the bundle; the ones inside the stdlib have to stay, which is why the
# runtime is sealed as resources rather than signed as nested code.
rm -f "$DEST/bin/pip"* "$DEST/bin/pydoc"* "$DEST"/bin/*-config

if [[ ! -x "$DEST/bin/python3" ]]; then
  echo "The bundled runtime has no bin/python3 — the archive layout changed." >&2
  exit 1
fi

# The archive ships almost no bytecode, and that matters twice over. The first
# import would write __pycache__ directories throughout the bundle: where the
# app is writable that invalidates its signature, and where it is not every
# launch re-parses the standard library from source. Compiling here puts the
# bytecode inside the signature instead. `unchecked-hash` tells Python to load
# it without comparing timestamps, so nothing tempts it to write again after
# the files have been copied, zipped, and copied once more.
"$DEST/bin/python3" -m compileall -q -f -j 0 \
  --invalidation-mode unchecked-hash "$DEST/lib/python3.12"

echo "Bundled Python $PYTHON_VERSION into ${DEST/#$ROOT\//} ($(du -sh "$DEST" | cut -f1), $(find "$DEST" -name '*.pyc' | wc -l | tr -d ' ') compiled)"
