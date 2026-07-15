#!/bin/bash
# Stop only processes previously started by Qwen Scribe.
set -u

APP_SUPPORT="$HOME/Library/Application Support/Qwen Scribe"
PIDFILE="$APP_SUPPORT/server.pid"
DICTATION_PIDFILE="$APP_SUPPORT/dictation.pid"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"Qwen Scribe\"" 2>/dev/null
}

stop_known_process() {
  local pidfile="$1"
  [ -f "$pidfile" ] || return 1
  local pid command
  pid="$(cat "$pidfile" 2>/dev/null)"
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command" in
    *"/Library/Application Support/Qwen Scribe/"*|*QwenScribeDictation*)
      kill "$pid" >/dev/null 2>&1 || true
      rm -f "$pidfile"
      return 0
      ;;
    *)
      rm -f "$pidfile"
      return 1
      ;;
  esac
}

stopped=0
if stop_known_process "$PIDFILE"; then stopped=1; fi
if stop_known_process "$DICTATION_PIDFILE"; then stopped=1; fi

if [ "$stopped" = "1" ]; then
  notify "Server stopped."
else
  notify "Server was not running."
fi
