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
    *"/Library/Application Support/Qwen Scribe/"*|\
    *"/Qwen Scribe.app/Contents/MacOS/QwenScribe"*|\
    *QwenScribeDictation*)
      kill "$pid" >/dev/null 2>&1 || true
      # A wedged MLX decode ignores SIGTERM until the current chunk finishes,
      # so confirm the exit instead of assuming it.
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" >/dev/null 2>&1 || break
        sleep 1
      done
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill -9 "$pid" >/dev/null 2>&1 || true
        sleep 1
      fi
      if kill -0 "$pid" >/dev/null 2>&1; then
        return 2   # still alive: keep the pidfile so a retry can find it
      fi
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
stubborn=0
for pidfile in "$PIDFILE" "$DICTATION_PIDFILE"; do
  stop_known_process "$pidfile"
  case "$?" in
    0) stopped=1 ;;
    2) stubborn=1 ;;
  esac
done

if [ "$stubborn" = "1" ]; then
  notify "Qwen Scribe would not stop. Quit it from Activity Monitor."
elif [ "$stopped" = "1" ]; then
  notify "Server stopped."
else
  notify "Server was not running."
fi
