"""What the native dictation helper reports, and what the page shows of it."""

from __future__ import annotations

import threading
import time

from . import config, settings

# Updated by the optional native macOS helper. The web interface uses this to
# show whether desktop dictation is running and which macOS permission still
# needs attention.
dictation_state: dict[str, object] = {
    "last_seen": 0.0,
    "accessibility": None,
    "input_monitoring": None,
    "microphone": None,
}
dictation_lock = threading.Lock()

# A helper that has not reported for this long is taken to have stopped.
HEARTBEAT_TIMEOUT_SECONDS = 30


def heartbeat(accessibility: bool | None, input_monitoring: bool | None,
              microphone: bool | None) -> None:
    with dictation_lock:
        dictation_state.update(
            last_seen=time.time(),
            accessibility=accessibility,
            input_monitoring=input_monitoring,
            microphone=microphone,
        )


def status() -> dict:
    with dictation_lock:
        state = dict(dictation_state)
    dictation = settings.current("dictation")
    return {
        "available": bool(
            state["last_seen"]
            and time.time() - state["last_seen"] < HEARTBEAT_TIMEOUT_SECONDS
        ),
        "accessibility": state["accessibility"],
        "input_monitoring": state["input_monitoring"],
        "microphone": state["microphone"],
        "hotkey": dictation["hotkey"],
        "shortcut": config.DICTATION_HOTKEYS[dictation["hotkey"]],
        "model": dictation["model"],
        "language": dictation["language"],
    }
