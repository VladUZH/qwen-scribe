"""Model sessions: lazy-loaded, cached, one at a time on the GPU."""

from __future__ import annotations

import threading

from . import config

_sessions: dict[str, object] = {}
_session_lock = threading.Lock()


def get_session(model_key: str):
    """Return a cached mlx_qwen3_asr Session for the requested model size."""
    from mlx_qwen3_asr import Session  # imported lazily: slow first import

    model_id = config.MODELS[model_key]
    with _session_lock:
        if model_id not in _sessions:
            _sessions[model_id] = Session(model=model_id)
        return _sessions[model_id]
