#!/bin/bash
# Qwen Scribe server launcher — invoked by the native main app process.
set -u

# Finder starts apps with a minimal PATH. Cover the common package managers
# directly: Homebrew (/opt/homebrew, /usr/local) and MacPorts (/opt/local).
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:$PATH"

# For anything else (Nix, Fink, custom prefixes), merge in the user's login
# shell PATH the same way find_python already consults it for Python.
merge_login_shell_path() {
  # A chatty shell rc can print arbitrary text before the value, so take the
  # last line and accept it only if it looks like a PATH: starts with an
  # absolute path and has no spaces or control characters.
  local shell_path dir
  shell_path="$("${SHELL:-/bin/zsh}" -l -c 'printf "%s\n" "$PATH"' 2>/dev/null | tail -1)"
  case "$shell_path" in
    /*) ;;
    *) return 0 ;;
  esac
  case "$shell_path" in
    *" "*|*[![:print:]]*) return 0 ;;
  esac
  while IFS= read -r dir; do
    [ -n "$dir" ] && [ -d "$dir" ] || continue
    case ":$PATH:" in
      *":$dir:"*) continue ;;
    esac
    PATH="$PATH:$dir"
  done < <(printf '%s\n' "$shell_path" | tr ':' '\n')
  export PATH
}
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1

URL="http://127.0.0.1:8990"
LOG="$HOME/Library/Logs/QwenScribe.log"
APP_SUPPORT="$HOME/Library/Application Support/Qwen Scribe"
RUNTIME_DIR="$APP_SUPPORT/runtime"
VENV_DIR="$RUNTIME_DIR/.venv"
PIDFILE="$APP_SUPPORT/server.pid"
RESOURCES_DIR="$(cd "$(dirname "$0")" && pwd)"

dialog() {
  /usr/bin/osascript - "$1" >/dev/null 2>&1 << 'APPLESCRIPT'
on run argv
  display dialog (item 1 of argv) with title "Qwen Scribe" buttons {"OK"} default button 1 with icon caution
end run
APPLESCRIPT
}

fail_with_log() {
  dialog "$1

Last log lines ($LOG):
$(tail -n 8 "$LOG" 2>/dev/null | cut -c1-200)"
  exit 1
}

if [ "$(uname -m)" != "arm64" ]; then
  dialog "Qwen Scribe requires an Apple Silicon Mac."
  exit 1
fi

MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$MACOS_MAJOR" -lt 14 ]; then
  dialog "Qwen Scribe requires macOS 14 or newer."
  exit 1
fi

if [ ! -f "$RESOURCES_DIR/server.py" ] || [ ! -f "$RESOURCES_DIR/requirements-lock.txt" ]; then
  dialog "This Qwen Scribe app is incomplete. Build it again from the source repository."
  exit 1
fi

mkdir -p "$APP_SUPPORT" "$HOME/Library/Logs" || exit 1

server_up() {
  curl -s -m 2 "$URL/api/config" 2>/dev/null | grep -q '"models"'
}

find_python() {
  local candidates=(
    /opt/homebrew/bin/python3
    /usr/local/bin/python3
    /opt/local/bin/python3
    "$HOME/.local/bin/python3"
  )
  local framework_python
  for framework_python in $(ls -d /Library/Frameworks/Python.framework/Versions/3.* 2>/dev/null | sort -Vr); do
    candidates+=("$framework_python/bin/python3")
  done
  local shell_python
  shell_python="$("${SHELL:-/bin/zsh}" -l -c 'command -v python3' 2>/dev/null | tail -1)"
  [ -n "$shell_python" ] && candidates+=("$shell_python")
  candidates+=("$(command -v python3 2>/dev/null)")

  local candidate
  for candidate in "${candidates[@]}"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if server_up; then
  open "$URL"
  exit 0
fi

echo "=== $(date) launching ===" >> "$LOG"

# Runs only on a cold start: spawning a login shell can cost a moment with a
# heavy shell rc, and the already-running fast path above should stay instant.
merge_login_shell_path

prepare_runtime() {
  mkdir -p "$RUNTIME_DIR/models" || return 1
  /bin/cp -f "$RESOURCES_DIR/server.py" "$RESOURCES_DIR/requirements.txt" "$RESOURCES_DIR/requirements-lock.txt" "$RUNTIME_DIR/" >> "$LOG" 2>&1 || return 1
  /usr/bin/ditto "$RESOURCES_DIR/static" "$RUNTIME_DIR/static" >> "$LOG" 2>&1 || return 1
}

if ! prepare_runtime; then
  fail_with_log "Could not prepare Qwen Scribe's local runtime."
fi

# Read pyvenv.cfg without executing an environment made by an old Python.
if [ -f "$VENV_DIR/pyvenv.cfg" ]; then
  VENV_VERSION="$(sed -n 's/^version = \([0-9][0-9.]*\).*/\1/p' "$VENV_DIR/pyvenv.cfg" | head -1)"
  VENV_MAJOR="${VENV_VERSION%%.*}"
  VENV_REST="${VENV_VERSION#*.}"
  VENV_MINOR="${VENV_REST%%.*}"
  if [ -n "$VENV_VERSION" ] && {
    [ "${VENV_MAJOR:-0}" -lt 3 ] ||
    { [ "${VENV_MAJOR:-0}" -eq 3 ] && [ "${VENV_MINOR:-0}" -lt 12 ]; }
  }; then
    OLD_VENV="$RUNTIME_DIR/.venv-python-${VENV_VERSION}-$(date +%Y%m%d-%H%M%S)"
    echo "Replacing incompatible Python $VENV_VERSION environment (saved as $OLD_VENV)." >> "$LOG"
    mv "$VENV_DIR" "$OLD_VENV"
  fi
fi

if [ -x "$VENV_DIR/bin/python" ] && ! "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
  BROKEN_VENV="$RUNTIME_DIR/.venv-broken-$(date +%Y%m%d-%H%M%S)"
  echo "Replacing unusable Python environment (saved as $BROKEN_VENV)." >> "$LOG"
  mv "$VENV_DIR" "$BROKEN_VENV"
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  /usr/bin/osascript -e 'display notification "First run: creating the private Python environment…" with title "Qwen Scribe"' 2>/dev/null
  created=0
  if PYBIN="$(find_python)"; then
    if "$PYBIN" -m venv "$VENV_DIR" >> "$LOG" 2>&1; then created=1; fi
  fi
  if [ "$created" = "0" ] && command -v uv >/dev/null 2>&1; then
    echo "venv failed or no Python was found; trying uv." >> "$LOG"
    if uv venv "$VENV_DIR" --python '>=3.12' >> "$LOG" 2>&1; then created=1; fi
  fi
  if [ "$created" = "0" ]; then
    fail_with_log "Failed to create the Python environment. Install Python 3.12 or newer and try again."
  fi
fi

REQ_HASH="$(/usr/bin/shasum -a 256 "$RUNTIME_DIR/requirements-lock.txt" | awk '{print $1}')"
REQ_MARKER="$VENV_DIR/.qwen-scribe-requirements.sha256"
INSTALLED_HASH="$(cat "$REQ_MARKER" 2>/dev/null || true)"
if [ "$REQ_HASH" != "$INSTALLED_HASH" ]; then
  /usr/bin/osascript -e 'display notification "Installing pinned dependencies…" with title "Qwen Scribe"' 2>/dev/null
  installed=0
  if [ -x "$VENV_DIR/bin/pip" ]; then
    "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet -r "$RUNTIME_DIR/requirements-lock.txt" >> "$LOG" 2>&1 && installed=1
  elif command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV_DIR/bin/python" -r "$RUNTIME_DIR/requirements-lock.txt" >> "$LOG" 2>&1 && installed=1
  fi
  if [ "$installed" = "0" ]; then
    fail_with_log "Dependency installation failed. Check your internet connection and the log."
  fi
  printf '%s\n' "$REQ_HASH" > "$REQ_MARKER"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  /usr/bin/osascript -e 'display notification "ffmpeg is missing — WAV works, but other media needs it (brew install ffmpeg / sudo port install ffmpeg)" with title "Qwen Scribe"' 2>/dev/null
fi

cd "$RUNTIME_DIR" || fail_with_log "Cannot open Qwen Scribe's local runtime."
nohup "$VENV_DIR/bin/python" "$RUNTIME_DIR/server.py" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"

for _ in $(seq 1 60); do
  if server_up; then
    open "$URL"
    /usr/bin/osascript -e 'display notification "Ready — hold Right Command to dictate anywhere." with title "Qwen Scribe"' 2>/dev/null
    exit 0
  fi
  sleep 1
done

fail_with_log "Server did not start within 60 seconds."
