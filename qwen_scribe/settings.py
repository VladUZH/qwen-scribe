"""Persisted settings.

The server is the single owner of persisted state, so the browser interface
and the native dictation helper cannot disagree: the helper polls this and
applies it, the page reads and writes it.
"""

from __future__ import annotations

import json
import threading

from . import config

SETTINGS_FILE = config.APP_DATA_DIR / "settings.json"
settings_lock = threading.Lock()

DEFAULT_SETTINGS = {
    # Polled and applied by the native dictation helper.
    "dictation": {
        "hotkey": "right_command",
        "model": config.DEFAULT_MODEL,
        "language": "auto",
        # Names and terms the model should expect; sent as the vocabulary
        # hint with every dictation, which is where Qwen3-ASR's hint support
        # earns its keep.
        "dictionary": "",
        # Every dictation is a transcript like any other and lands in history
        # unless this is off, in which case it exists only long enough for
        # the helper to collect and paste it.
        "save_history": True,
    },
    # The file-transcription pane's choices. Kept on the server rather than in
    # the browser so they survive a cleared cache and a different browser.
    "transcription": {
        "model": config.DEFAULT_MODEL,
        "language": "auto",
        "timestamps": False,
        "turbo": False,
        "context": "",
        "sentence_per_line": False,
    },
}


def _one_of(allowed):
    # The isinstance check matters: a list/dict value would raise TypeError
    # (unhashable) from the membership test, not ValueError.
    return lambda value: isinstance(value, str) and value in allowed


def _boolean(value) -> bool:
    return isinstance(value, bool)


def _short_text(value) -> bool:
    return isinstance(value, str) and len(value) <= config.MAX_CONTEXT_CHARS


# Per-section, per-key value validators. A key absent from its section's map
# is rejected outright, so a typo can never be silently persisted.
_SECTION_VALIDATORS = {
    "dictation": {
        "hotkey": _one_of(config.DICTATION_HOTKEYS),
        "model": _one_of(config.MODELS),
        "language": _one_of(config.LANGUAGES),
        "dictionary": _short_text,
        "save_history": _boolean,
    },
    "transcription": {
        "model": _one_of(config.MODELS),
        "language": _one_of(config.LANGUAGES),
        "timestamps": _boolean,
        "turbo": _boolean,
        "context": _short_text,
        "sentence_per_line": _boolean,
    },
}


def _validated_section(section: str, candidate: object, base: dict) -> dict:
    """Merge candidate onto base, rejecting unknown keys and values."""
    validators = _SECTION_VALIDATORS[section]
    merged = dict(base)
    if not isinstance(candidate, dict):
        raise ValueError(f"'{section}' must be an object")
    for key, value in candidate.items():
        check = validators.get(key)
        if check is None:
            raise ValueError(f"Unknown {section} setting '{key}'")
        if not check(value):
            raise ValueError(f"Invalid value for '{key}': {value!r}")
        merged[key] = value
    return merged


def _load_settings() -> dict:
    """Read settings from disk, falling back field by field to the defaults."""
    settings = {name: dict(values) for name, values in DEFAULT_SETTINGS.items()}
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(stored, dict):
        return settings
    for section in DEFAULT_SETTINGS:
        section_values = stored.get(section)
        if not isinstance(section_values, dict):
            continue
        # Field by field: one hand-edited bad value must not take the server
        # down or discard the other stored settings.
        for key, value in section_values.items():
            try:
                settings[section] = _validated_section(
                    section, {key: value}, settings[section]
                )
            except ValueError:
                continue
    return settings


def _save_settings(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = SETTINGS_FILE.with_suffix(".json.tmp")
    try:
        temporary_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(SETTINGS_FILE)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


# Module state: the live settings every request and the helper's poll read.
_settings = _load_settings()


def current(section: str) -> dict:
    """A copy of one section, taken under the lock."""
    with settings_lock:
        return dict(_settings[section])


def response() -> dict:
    """The full settings body, plus the options the pickers are built from."""
    with settings_lock:
        sections = {name: dict(_settings[name]) for name in DEFAULT_SETTINGS}
    return {
        **sections,
        "options": {
            "hotkeys": [
                {"id": key, "label": label}
                for key, label in config.DICTATION_HOTKEYS.items()
            ],
            "models": list(config.MODELS.keys()),
            "languages": config.LANGUAGES,
        },
    }


def update(payload: dict) -> dict:
    """Apply a partial update and return the full body.

    Raises ValueError for anything invalid, before any section is touched, so
    a bad value in one cannot half-apply the payload. Raises OSError when the
    file cannot be written; the live values are left alone in that case, so a
    setting the helper applies is never one a restart would forget.
    """
    with settings_lock:
        unknown = set(payload) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"Unknown settings section '{sorted(unknown)[0]}'")
        merged = {
            name: _validated_section(name, payload[name], _settings[name])
            if name in payload
            else dict(_settings[name])
            for name in DEFAULT_SETTINGS
        }
        # Persist first, then commit to memory.
        _save_settings(merged)
        _settings.update(merged)
    return response()
