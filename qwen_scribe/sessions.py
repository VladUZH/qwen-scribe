"""Model sessions: lazy-loaded, cached, one at a time on the GPU.

Also owns the two things around a session that users feel: the first-run
download, reported with real numbers instead of a bare "loading", and the
unloading of a model nobody has used for a while, so the memory goes back
to the Mac instead of staying claimed until quit.
"""

from __future__ import annotations

import fnmatch
import gc
import io
import sys
import threading
import time
from pathlib import Path

from . import config

# The same patterns mlx-qwen3-asr fetches, so a pre-download leaves nothing
# for its own call to fetch and the two can never disagree about a file.
WEIGHT_PATTERNS = ["*.json", "*.safetensors", "*.txt", "*.model"]

_sessions: dict[str, object] = {}
_last_used: dict[str, float] = {}
_session_lock = threading.Lock()


def _model_id(model_key: str) -> str:
    return config.model_source(model_key)


def _is_local(model_id: str) -> bool:
    return Path(model_id).exists()


def get_session(model_key: str):
    """Return a cached mlx_qwen3_asr Session for the requested model size."""
    from mlx_qwen3_asr import Session  # imported lazily: slow first import

    model_id = _model_id(model_key)
    with _session_lock:
        if model_id not in _sessions:
            _sessions[model_id] = Session(model=model_id)
        _last_used[model_id] = time.time()
        return _sessions[model_id]


def loaded_models() -> list[str]:
    """The model keys currently held in memory."""
    with _session_lock:
        held = set(_sessions)
    return [key for key in config.MODEL_CATALOG if config.model_source(key) in held]


def drop(model_id: str) -> bool:
    """Unload one model by its source id; True when it was loaded."""
    with _session_lock:
        was_loaded = _sessions.pop(model_id, None) is not None
        _last_used.pop(model_id, None)
    if was_loaded:
        _release_memory()
    return was_loaded


def drop_idle(idle_seconds: float, now: float | None = None) -> list[str]:
    """Unload sessions unused for at least idle_seconds; returns their ids."""
    now = time.time() if now is None else now
    with _session_lock:
        stale = [
            model_id for model_id, used_at in _last_used.items()
            if model_id in _sessions and now - used_at >= idle_seconds
        ]
        for model_id in stale:
            _sessions.pop(model_id, None)
            _last_used.pop(model_id, None)
    if stale:
        _release_memory()
    return stale


def drop_all() -> list[str]:
    with _session_lock:
        dropped = list(_sessions)
        _sessions.clear()
        _last_used.clear()
    if dropped:
        _release_memory()
    return dropped


def _release_memory() -> None:
    # Dropping the Python objects is what frees the weights; the collector
    # makes it prompt, and MLX keeps a buffer cache of its own worth clearing.
    gc.collect()
    # Only if MLX is already loaded: a session was created through it, so it
    # is. Importing it here instead would load a native extension purely to
    # clear an empty cache, and inside a test that swaps modules in and out
    # of sys.modules a re-import of that extension aborts the interpreter.
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return
    try:
        mx.clear_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# First-run download with progress
# ---------------------------------------------------------------------------

def _download_plan(model_id: str) -> tuple[int, int]:
    """(bytes still to fetch, total bytes) for the files the library wants.

    Any failure, an offline Mac included, answers (0, 0): the library's own
    fetch then behaves exactly as it always has, falling back to its cache.
    """
    try:
        from huggingface_hub import HfApi, try_to_load_from_cache

        info = HfApi().model_info(model_id, files_metadata=True)
        to_download = total = 0
        for sibling in info.siblings or []:
            name = sibling.rfilename
            if not any(fnmatch.fnmatch(name, pattern) for pattern in WEIGHT_PATTERNS):
                continue
            size = int(sibling.size or 0)
            total += size
            # A str is a cached path; anything else means not cached.
            if not isinstance(try_to_load_from_cache(model_id, name), str):
                to_download += size
        return to_download, total
    except Exception:
        return 0, 0


class _Progress:
    """Aggregates the per-file byte counters huggingface_hub reports through
    its tqdm class into one running total for the job's status line."""

    def __init__(self, callback, total: int):
        self.callback = callback
        self.total = total
        self.done = 0
        self._lock = threading.Lock()
        self._last_report = 0.0

    def advance(self, n: int) -> None:
        with self._lock:
            self.done += n
            now = time.time()
            # A 4 GB download reports thousands of times; once every half
            # second is plenty for a status line, but the last one always goes.
            if now - self._last_report < 0.5 and self.done < self.total:
                return
            self._last_report = now
            done, total = self.done, self.total
        if self.callback is not None:
            self.callback(done, max(total, done))

    def tqdm_class(self):
        from huggingface_hub.utils import tqdm as hub_tqdm

        progress = self

        class Reporting(hub_tqdm):
            def __init__(self, *args, **kwargs):
                # Only the per-file bars count bytes; the "Fetching N files"
                # bar counts files and would corrupt the total.
                self._counts_bytes = kwargs.get("unit") == "B"
                # Nothing should print to the server's log.
                kwargs["file"] = io.StringIO()
                super().__init__(*args, **kwargs)

            def update(self, n=1):
                super().update(n)
                if self._counts_bytes and n:
                    progress.advance(int(n))

        return Reporting


def ensure_downloaded(model_key: str, progress=None) -> None:
    """Fetch the weights the session will need, reporting bytes as they land.

    ``progress(done_bytes, total_bytes)`` is called as the download advances.
    A model that is local, already loaded, or fully cached returns at once,
    so this adds nothing to the second run.
    """
    model_id = _model_id(model_key)
    if _is_local(model_id):
        return
    with _session_lock:
        if model_id in _sessions:
            return
    to_download, total = _download_plan(model_id)
    if to_download <= 0:
        return
    from huggingface_hub import snapshot_download

    reporter = _Progress(progress, to_download)
    if progress is not None:
        progress(0, to_download)
    snapshot_download(
        repo_id=model_id,
        allow_patterns=WEIGHT_PATTERNS,
        tqdm_class=reporter.tqdm_class(),
    )
