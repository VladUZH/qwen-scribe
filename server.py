"""
Qwen Scribe — local transcription server for Apple Silicon.

FastAPI wrapper around mlx-qwen3-asr (Qwen3-ASR running natively on Metal).
Upload audio or video in the browser at http://localhost:8990 — nothing
leaves your machine.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Deliberately fixed: this API has no remote-user authentication and must not
# be exposed to a LAN or the public internet.
HOST = "127.0.0.1"
PORT = int(os.environ.get("QWEN_SCRIBE_PORT", "8990"))

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = Path(os.environ.get("QWEN_SCRIBE_MODEL_DIR", BASE_DIR / "models")).expanduser()

# Completed transcripts live outside the project folder so they survive app
# upgrades, moves, and browser-cache clearing. Tests and portable installs can
# override the location with QWEN_SCRIBE_DATA_DIR.
APP_DATA_DIR = Path(
    os.environ.get(
        "QWEN_SCRIBE_DATA_DIR",
        Path.home() / "Library" / "Application Support" / "Qwen Scribe",
    )
).expanduser()
TRANSCRIPTS_DIR = APP_DATA_DIR / "transcripts"
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

# If quantize_8bit.py has produced a local 8-bit model, use it for "1.7b".
_QUANT_1_7B = MODEL_DIR / "qwen3-asr-1.7b-8bit"
_QUANT_ACTIVE = (_QUANT_1_7B / "quantization_config.json").exists()

MODELS = {
    "1.7b": str(_QUANT_1_7B) if _QUANT_ACTIVE else "Qwen/Qwen3-ASR-1.7B",
    "0.6b": "Qwen/Qwen3-ASR-0.6B",   # speed-first, ~1.2 GB
}
DEFAULT_MODEL = "1.7b"

# Every language Qwen3-ASR supports, so the picker never hides one that works
# — mlx_qwen3_asr.tokenizer.known_language_names(). "auto" lets the model
# detect it. Word timestamps for Japanese and Korean need the tokenizers from
# the `aligner` extra, which requirements-lock.txt pins.
LANGUAGES = [
    "auto", "Arabic", "Chinese", "Dutch", "English", "French", "German",
    "Hindi", "Italian", "Japanese", "Korean", "Portuguese", "Russian",
    "Spanish", "Turkish",
]

# mlx-qwen3-asr deliberately joins these languages without adding whitespace
# between independently decoded chunks. Keep this in sync with its public
# CJK language aliases so a long auto-detected transcript is not altered at
# every 30-second boundary.
UNSPACED_LANGUAGE_ALIASES = {
    "chinese", "zh", "zh-cn", "zh-tw", "cantonese", "yue",
    "japanese", "ja", "jp", "korean", "ko", "kr",
}

# Push-to-talk keys the native helper can watch. The ids must match the
# key-code/modifier-mask table in native/DictationHelper.m. Only right-side
# modifiers qualify: they have unambiguous device-dependent bits and macOS
# never synthesizes them around other keys (Fn is synthesized around every
# arrow/navigation key, so offering it would start dictation on PageUp).
DICTATION_HOTKEYS = {
    "right_command": "Right ⌘",
    "right_option": "Right ⌥",
    "right_control": "Right ⌃",
}

ALLOWED_SUFFIXES = {
    # audio
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma", ".aiff",
    ".qta",  # Apple Voice Memos (QuickTime container; needs ffmpeg >= 7 to
             # skip the undecodable APAC spatial track and use the AAC one)
    # video (audio track is extracted via ffmpeg)
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
}

MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
# Multipart boundaries and part headers add a little to the declared body size.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
OVERSIZE_DETAIL = (
    "File exceeds the 4 GB upload limit. The file is copied to a temporary "
    "folder before transcription, so the cap keeps one job from needing twice "
    "its size in free disk space. It is not a model limit: extract the audio "
    "track and transcribe that instead — "
    "ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 output.wav"
)

UPLOAD_DIR = Path(tempfile.gettempdir()) / "qwen-scribe-uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# A hard crash can bypass a job's finally block, and can leave a half-written
# transcript behind. Remove only old files so a second accidental server
# process cannot interfere with an active upload.
def _sweep_stale(paths, max_age_seconds: float = 24 * 60 * 60) -> None:
    for stale in paths:
        try:
            if stale.is_file() and time.time() - stale.stat().st_mtime > max_age_seconds:
                stale.unlink()
        except OSError:
            pass


_sweep_stale(UPLOAD_DIR.iterdir())
_sweep_stale(TRANSCRIPTS_DIR.glob("*.json.tmp"))

# ---------------------------------------------------------------------------
# Model sessions (lazy-loaded, cached, one at a time on the GPU)
# ---------------------------------------------------------------------------

_sessions: dict[str, object] = {}
_session_lock = threading.Lock()


def get_session(model_key: str):
    """Return a cached mlx_qwen3_asr Session for the requested model size."""
    from mlx_qwen3_asr import Session  # imported lazily: slow first import

    model_id = MODELS[model_key]
    with _session_lock:
        if model_id not in _sessions:
            _sessions[model_id] = Session(model=model_id)
        return _sessions[model_id]


# ---------------------------------------------------------------------------
# Job store + worker
# ---------------------------------------------------------------------------

jobs: dict[str, dict] = {}
job_futures: dict[str, Future] = {}
jobs_lock = threading.Lock()

# Never sent to the browser: the staged upload path, and a threading.Event
# that is not JSON-serialisable anyway.
_PRIVATE_JOB_FIELDS = {"path", "cancelled"}

# Per-run fields a retry must not inherit from the job it repeats. Everything
# else — the filename, model, language, timestamps, turbo, vocabulary — is
# exactly what the user chose the first time and is carried over.
_RESET_ON_RETRY = {
    "id", "status", "detail", "progress", "partial", "result", "path",
    "cancelled", "cancel_requested", "retried_as", "created_at", "started_at",
    "finished_at", "timestamps_unavailable", "history_saved", "history_error",
}


# Retry ids that have been claimed but not yet registered. Staging the copy
# happens outside jobs_lock, so for its duration — the whole point of moving it
# out, on a multi-gigabyte file — the new job is in neither place. Without this
# a second Retry during the copy would find nothing to collide with.
retries_staging: set[str] = set()


class _JobCancelled(Exception):
    """Raised inside the worker when the user cancels a running job."""


def _public_job(job: dict) -> dict:
    return {key: value for key, value in job.items() if key not in _PRIVATE_JOB_FIELDS}

# Updated by the optional native macOS helper. The web UI uses this to show
# whether right-Command desktop dictation is running and which macOS permission
# still needs attention.
dictation_state: dict[str, object] = {
    "last_seen": 0.0,
    "accessibility": None,
    "input_monitoring": None,
    "microphone": None,
}
dictation_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Settings (persisted; the native dictation helper polls and applies them)
# ---------------------------------------------------------------------------

SETTINGS_FILE = APP_DATA_DIR / "settings.json"
settings_lock = threading.Lock()

DEFAULT_SETTINGS = {
    # Polled and applied by the native dictation helper.
    "dictation": {
        "hotkey": "right_command",
        "model": DEFAULT_MODEL,
        "language": "auto",
    },
    # The file-transcription pane's choices. Kept on the server rather than in
    # the browser so they survive a cleared cache and a different browser.
    "transcription": {
        "model": DEFAULT_MODEL,
        "language": "auto",
        "timestamps": False,
        "turbo": False,
        "context": "",
        "sentence_per_line": False,
    },
}

MAX_CONTEXT_CHARS = 2000


def _one_of(allowed):
    # The isinstance check matters: a list/dict value would raise TypeError
    # (unhashable) from the membership test, not ValueError.
    return lambda value: isinstance(value, str) and value in allowed


def _boolean(value) -> bool:
    return isinstance(value, bool)


def _short_text(value) -> bool:
    return isinstance(value, str) and len(value) <= MAX_CONTEXT_CHARS


# Per-section, per-key value validators. A key absent from its section's map
# is rejected outright, so a typo can never be silently persisted.
_SECTION_VALIDATORS = {
    "dictation": {
        "hotkey": _one_of(DICTATION_HOTKEYS),
        "model": _one_of(MODELS),
        "language": _one_of(LANGUAGES),
    },
    "transcription": {
        "model": _one_of(MODELS),
        "language": _one_of(LANGUAGES),
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


_settings = _load_settings()

# One worker: serialize GPU work so parallel uploads queue instead of thrash.
executor = ThreadPoolExecutor(max_workers=1)

# Set on shutdown so a running transcription unwinds at the next chunk
# boundary instead of holding the process open for the rest of a long file.
stopping = threading.Event()

# Finished jobs are kept only long enough for the browser to collect the
# result; the transcript itself lives in TRANSCRIPTS_DIR.
JOB_RETENTION_SECONDS = 60 * 60
MAX_REMEMBERED_JOBS = 50
# A job in one of these is settled: safe to evict, no longer cancellable, and
# never resurrected by a worker returning from an in-flight model call.
TERMINAL_JOB_STATUSES = {"done", "error", "cancelled"}


def _transcript_path(transcript_id: str) -> Path:
    """Return a safe transcript path, rejecting path traversal and bad IDs."""
    if not re.fullmatch(r"[a-f0-9]{12}", transcript_id):
        raise HTTPException(404, "Transcript not found")
    return TRANSCRIPTS_DIR / f"{transcript_id}.json"


def _save_transcript(job: dict, result: dict, finished_at: float) -> None:
    """Atomically persist one completed transcript as human-readable JSON."""
    transcript = {
        "id": job["id"],
        "filename": job.get("filename") or "Untitled recording",
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": finished_at,
        "model": job.get("model"),
        "language_requested": job.get("language"),
        "timestamps_requested": bool(job.get("timestamps")),
        # Set when timestamps were asked for but the aligner could not produce
        # them, so reopening the transcript still explains the missing .srt.
        "timestamps_unavailable": job.get("timestamps_unavailable"),
        "turbo": bool(job.get("turbo")),
        "context": job.get("context") or "",
        "result": result,
    }
    path = _transcript_path(job["id"])
    temporary_path = path.with_suffix(".json.tmp")
    try:
        # Flush to the platter before renaming: a rename of a still-buffered
        # file can survive a power loss as a zero-length transcript.
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(transcript, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _finite_json_float(value: str) -> float | None:
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _read_transcript(path: Path) -> dict:
    # Python accepts JavaScript's NaN/Infinity tokens even though JSON does
    # not, and a huge exponent silently becomes infinity. Treat both forms as
    # damaged null values so APIs and exports remain strict JSON.
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda _value: None,
        parse_float=_finite_json_float,
    )


def _timestamp(value: object) -> float | None:
    """Return value as a float only if it really is a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        timestamp = float(value)
    except (OverflowError, ValueError):
        return None
    # Python's JSON decoder accepts NaN/Infinity, but Starlette intentionally
    # refuses to serialize them. Bounds also keep hand-edited dates within the
    # range supported by Python/JavaScript date formatting (years 1..9999).
    if not math.isfinite(timestamp) or not -62_135_596_800 <= timestamp <= 253_402_300_799:
        return None
    return timestamp


def _transcript_summary(transcript: dict) -> dict:
    # Hand-edited or truncated files reach this function too, so every field
    # is treated as untrusted: one bad file must not break the whole history.
    if not isinstance(transcript, dict):
        raise ValueError("transcript file does not contain a JSON object")
    result = transcript.get("result")
    result = result if isinstance(result, dict) else {}
    text = result.get("text")
    text = text if isinstance(text, str) else ""
    normalized = " ".join(text.split())
    started_at = _timestamp(transcript.get("started_at"))
    finished_at = _timestamp(transcript.get("finished_at"))
    duration_seconds = None
    if started_at is not None and finished_at is not None:
        duration_seconds = max(0.0, finished_at - started_at)
    return {
        "id": transcript.get("id"),
        "filename": transcript.get("filename") or "Untitled recording",
        "created_at": _timestamp(transcript.get("created_at")),
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "model": transcript.get("model"),
        "language": result.get("language"),
        "word_count": len(text.split()),
        "preview": normalized[:220],
        "has_timestamps": bool(result.get("segments")),
        "truncated": bool(result.get("truncated")),
    }


def _update(job_id: str, **fields) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        # Shutdown and normal completion are terminal ownership decisions. A
        # worker returning from an in-flight model call must never resurrect a
        # job that shutdown already marked as failed.
        if job is None or job.get("status") in TERMINAL_JOB_STATUSES:
            return
        if fields.get("status") in TERMINAL_JOB_STATUSES:
            fields.setdefault("finished_at", time.time())
        job.update(fields)
        if fields.get("status") in TERMINAL_JOB_STATUSES:
            _prune_jobs_locked()


def _forget_future(job_id: str) -> None:
    with jobs_lock:
        job_futures.pop(job_id, None)


def _join_transcript_texts(texts: list[str], language: str | None) -> str:
    normalized = (language or "").strip().lower()
    return ("" if normalized in UNSPACED_LANGUAGE_ALIASES else " ").join(texts)


def _searchable(value: str) -> str:
    """Normalize text for user-facing, Unicode-aware transcript search."""
    return unicodedata.normalize("NFKC", value).casefold()


def _prune_jobs_locked() -> None:
    """Drop old finished jobs. Caller must hold jobs_lock.

    Completed transcripts are already on disk, so the in-memory record only
    has to outlive the browser's polling loop. Running jobs are never evicted:
    the UI would see a 404 mid-transcription.
    """
    now = time.time()
    finished = [
        (job.get("finished_at") or job.get("created_at") or 0.0, job_id)
        for job_id, job in jobs.items()
        if job.get("status") in TERMINAL_JOB_STATUSES
    ]
    evict = {job_id for stamp, job_id in finished if now - stamp > JOB_RETENTION_SECONDS}
    surviving = sorted((item for item in finished if item[1] not in evict), reverse=True)
    evict.update(job_id for _stamp, job_id in surviving[MAX_REMEMBERED_JOBS:])
    for job_id in evict:
        # A failed job keeps its upload so it can be retried; forgetting the
        # job is the last moment anything still knows to delete the file.
        retained = jobs.get(job_id, {}).get("path")
        if retained:
            Path(retained).unlink(missing_ok=True)
        jobs.pop(job_id, None)


def _release_retry_claim(job_id: str, new_id: str) -> None:
    """Undo a retry claim that never became a job. Caller must hold jobs_lock."""
    retries_staging.discard(new_id)
    source = jobs.get(job_id)
    if source is not None and source.get("retried_as") == new_id:
        source.pop("retried_as")


def _forget_jobs(*job_ids: str) -> None:
    """Drop finished jobs whose transcript the user just deleted."""
    with jobs_lock:
        for job_id in job_ids:
            if jobs.get(job_id, {}).get("status") in TERMINAL_JOB_STATUSES:
                del jobs[job_id]


def _run_job(job_id: str) -> None:
    with jobs_lock:
        source = jobs.get(job_id)
        # Cancelling settles a queued job immediately, which makes it terminal
        # and so evictable by the remembered-job cap before the single worker
        # ever reaches it. Its upload is already gone with it.
        if source is None:
            return
        job = dict(source)

    path = Path(job["path"])
    cancelled = job["cancelled"]

    try:
        if cancelled.is_set():
            # Cancelled while it sat in the queue: never load a model for it.
            raise _JobCancelled
        if stopping.is_set():
            raise RuntimeError("Server stopped before this job started")
        _update(job_id, status="loading", detail="Loading model (first run downloads weights)")
        # Loading can download several gigabytes of weights. A cancel that
        # arrived while this job was still queued must not pay for that.
        if cancelled.is_set():
            raise _JobCancelled
        session = get_session(job["model"])

        # Speculative decoding: the 0.6B model drafts tokens, the 1.7B verifies
        # them in parallel — more GPU utilization, same output quality.
        draft_model = None
        if job["turbo"] and job["model"] == "1.7b":
            _update(job_id, detail="Loading 0.6B draft model for speculative decoding")
            # Pass the Session-owned model itself. Passing its string id makes
            # mlx-qwen3-asr load a second independent 0.6B copy into its global
            # model holder, wasting roughly another 1.2 GB of unified memory.
            draft_model = get_session("0.6b").model

        _update(job_id, detail="Decoding audio")
        from mlx_qwen3_asr.audio import load_audio_np
        from mlx_qwen3_asr.chunking import split_audio_into_chunks

        SR = 16000
        audio = load_audio_np(str(path), sr=SR)  # ffmpeg handles video too
        total_sec = len(audio) / SR
        chunks = split_audio_into_chunks(audio, SR, 30.0)  # energy-minima splits

        started_at = time.time()
        _update(
            job_id, status="processing", started_at=started_at,
            detail=f"Transcribing {total_sec/60:.1f} min in {len(chunks)} chunks",
        )

        kwargs: dict = {"return_timestamps": job["timestamps"]}
        if job["language"] != "auto":
            kwargs["language"] = job["language"]
        if job["context"]:
            kwargs["context"] = job["context"]
        if draft_model is not None:
            kwargs["draft_model"] = draft_model

        texts: list[str] = []
        segments: list[dict] = []
        languages: list[str] = []
        truncated = False
        processed_sec = 0.0
        timestamps_unavailable: str | None = None

        # split_audio_into_chunks returns (waveform, offset_seconds) tuples.
        for i, (chunk_audio, chunk_offset) in enumerate(chunks):
            if cancelled.is_set():
                raise _JobCancelled
            if stopping.is_set():
                raise RuntimeError("Server stopped before this job finished")
            try:
                result = session.transcribe(chunk_audio, **kwargs)
            except Exception as exc:
                # A word-timestamp backend failure — a missing CJK tokenizer, a
                # bad aligner asset — must cost the timestamps, not the whole
                # transcript. Retrying the chunk without them is also how a
                # timestamp failure is told apart from a dead decoder: only a
                # retry that succeeds proves the aligner was the problem.
                if not kwargs.get("return_timestamps"):
                    raise
                if cancelled.is_set():
                    raise _JobCancelled from exc
                if stopping.is_set():
                    raise RuntimeError("Server stopped before this job finished") from exc
                try:
                    result = session.transcribe(
                        chunk_audio, **{**kwargs, "return_timestamps": False}
                    )
                except Exception:
                    raise exc
                # Stay timestamp-free for the rest of the file, so a two-hour
                # recording does not fail twice per chunk. Timestamps already
                # collected go too: half an .srt ends mid-recording with
                # nothing on screen saying where it stopped.
                kwargs["return_timestamps"] = False
                segments.clear()
                timestamps_unavailable = (
                    f"Word timestamps are unavailable for this audio "
                    f"({type(exc).__name__}: {exc}). The transcript itself is complete."
                )
                _update(job_id, timestamps_unavailable=timestamps_unavailable)
            # The model call itself cannot be interrupted. Recheck immediately
            # afterwards so its final chunk cannot complete a job after a stop
            # or a cancel.
            if cancelled.is_set():
                raise _JobCancelled
            if stopping.is_set():
                raise RuntimeError("Server stopped before this job finished")
            if result.text:
                texts.append(result.text.strip())
            if result.language:
                languages.append(result.language)
            if result.segments:
                for seg in result.segments:
                    segments.append(
                        {**seg,
                         "start": seg["start"] + chunk_offset,
                         "end": seg["end"] + chunk_offset}
                    )
            truncated = truncated or bool(getattr(result, "truncated", False))
            processed_sec = chunk_offset + len(chunk_audio) / SR

            _update(
                job_id,
                progress=processed_sec / total_sec if total_sec else 1.0,
                detail=f"Chunk {i + 1}/{len(chunks)} · {processed_sec/60:.1f}/{total_sec/60:.1f} min",
                partial=_join_transcript_texts(
                    texts,
                    Counter(languages).most_common(1)[0][0] if languages else None,
                ),
            )

        # Counter preserves first-seen order for ties; the previous set-based
        # expression could report a different language between processes.
        language = Counter(languages).most_common(1)[0][0] if languages else None
        finished_at = time.time()
        result = {
            "text": _join_transcript_texts(texts, language),
            "language": language,
            # word-level [{text,start,end}]
            "segments": None if timestamps_unavailable else (segments or None),
            "truncated": truncated,
        }
        history_saved = True
        history_error = None
        completed_job = {
            **job,
            "started_at": started_at,
            "timestamps_unavailable": timestamps_unavailable,
        }
        # Serialize the final durable write with shutdown. Whichever acquires
        # jobs_lock first owns the outcome: either this transcript is fully
        # saved and marked done, or shutdown marks it error and no file appears.
        with jobs_lock:
            current = jobs.get(job_id)
            # A cancel that arrived after the last chunk's check is still a
            # cancel: without this the API has already answered "cancelling"
            # and the job would go on to save itself to history as done.
            if cancelled.is_set():
                raise _JobCancelled
            if (
                stopping.is_set()
                or current is None
                or current.get("status") in TERMINAL_JOB_STATUSES
            ):
                raise RuntimeError("Server stopped before this job finished")
            try:
                _save_transcript(completed_job, result, finished_at)
            except Exception as exc:
                # Never discard a successful transcription merely because its
                # history file could not be written. The UI surfaces this warning.
                history_saved = False
                history_error = f"Could not save transcript: {type(exc).__name__}: {exc}"
            current.update(
                status="done",
                progress=1.0,
                detail="Done",
                partial=None,
                finished_at=finished_at,
                result=result,
                history_saved=history_saved,
                history_error=history_error,
                timestamps_unavailable=timestamps_unavailable,
            )
            _prune_jobs_locked()
    except _JobCancelled:
        _update(job_id, status="cancelled", detail="Cancelled", partial=None,
                finished_at=time.time())
    except Exception as exc:  # surface the real cause to the UI
        # A job the user stopped is cancelled even when the interruption
        # surfaced as an error: reporting a failure they caused, and keeping
        # the upload staged for a retry they never asked for, is just noise.
        if cancelled.is_set():
            _update(job_id, status="cancelled", detail="Cancelled", partial=None,
                    finished_at=time.time())
        else:
            _update(
                job_id,
                status="error",
                detail=f"{type(exc).__name__}: {exc}",
                finished_at=time.time(),
            )
    finally:
        # A job that failed on its own keeps its upload so "Retry" does not
        # need a re-upload; _prune_jobs_locked deletes it when the job is
        # finally forgotten. A shutdown is not a retryable failure, and must
        # leave nothing staged behind.
        with jobs_lock:
            retry_pending = (
                not stopping.is_set()
                and jobs.get(job_id, {}).get("status") == "error"
            )
        if not retry_pending:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    stopping.clear()
    yield
    # Without this, Python's atexit handler joins the worker thread and
    # silently runs every queued job to completion before the process exits.
    # Mark unfinished jobs while holding the same lock used for the final
    # transcript write. That makes shutdown and successful persistence a
    # single, deterministic ownership decision.
    with jobs_lock:
        stopping.set()
        finished_at = time.time()
        for job in jobs.values():
            if job.get("status") in {"queued", "loading", "processing"}:
                job.update(
                    status="error",
                    detail="Server stopped before this job finished",
                    finished_at=finished_at,
                )
        # executor.shutdown(cancel_futures=True) cancels work that never
        # entered _run_job, so its finally block cannot remove those staged
        # uploads. Snapshot them for explicit cancellation and cleanup.
        pending = [
            (future, Path(jobs[job_id]["path"]))
            for job_id, future in job_futures.items()
            if job_id in jobs and jobs[job_id].get("path")
        ]
    for future, path in pending:
        if future.cancel():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Qwen Scribe", docs_url=None, redoc_url=None, lifespan=lifespan)


def _with_security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "microphone=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'",
    )
    return response


@app.middleware("http")
async def local_requests_only(request: Request, call_next):
    """Reject DNS rebinding and cross-site browser requests to the local API."""
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    if HOST not in {"0.0.0.0", "::"}:
        allowed_hosts.add(HOST)

    if (request.url.hostname or "").lower() not in allowed_hosts:
        return _with_security_headers(
            JSONResponse({"detail": "Untrusted Host header"}, status_code=400)
        )

    origin = request.headers.get("origin")
    if origin:
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin.rstrip("/") != expected_origin.rstrip("/"):
            return _with_security_headers(
                JSONResponse(
                    {"detail": "Cross-origin requests are not allowed"},
                    status_code=403,
                )
            )

    # Refuse an oversized upload before Starlette spools the whole body to
    # disk. The per-chunk counter in create_job stays as the real limit for
    # requests that arrive without a Content-Length.
    if request.method == "POST" and request.url.path == "/api/jobs":
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES:
            return _with_security_headers(
                JSONResponse({"detail": OVERSIZE_DETAIL}, status_code=413)
            )

    return _with_security_headers(await call_next(request))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/config")
def config() -> dict:
    return {
        "models": list(MODELS.keys()),
        "default_model": DEFAULT_MODEL,
        "languages": LANGUAGES,
        "extensions": sorted(ALLOWED_SUFFIXES),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "quantized": _QUANT_ACTIVE,
    }


def _settings_response() -> dict:
    with settings_lock:
        sections = {name: dict(_settings[name]) for name in DEFAULT_SETTINGS}
    return {
        **sections,
        "options": {
            "hotkeys": [
                {"id": key, "label": label} for key, label in DICTATION_HOTKEYS.items()
            ],
            "models": list(MODELS.keys()),
            "languages": LANGUAGES,
        },
    }


@app.get("/api/settings")
def get_settings() -> dict:
    return _settings_response()


@app.put("/api/settings")
def update_settings(payload: dict) -> dict:
    with settings_lock:
        # Validate every section before committing any of them, so a bad value
        # in one cannot half-apply the payload.
        try:
            unknown = set(payload) - set(DEFAULT_SETTINGS)
            if unknown:
                raise ValueError(f"Unknown settings section '{sorted(unknown)[0]}'")
            merged = {
                name: _validated_section(name, payload[name], _settings[name])
                if name in payload
                else dict(_settings[name])
                for name in DEFAULT_SETTINGS
            }
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        # Persist first, then commit to memory: a failed write must not leave
        # a live value the dictation helper applies but a restart forgets.
        try:
            _save_settings(merged)
        except OSError as exc:
            raise HTTPException(500, f"Could not save settings: {exc}") from exc
        _settings.update(merged)
    return _settings_response()


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    language: str = Form("English"),
    timestamps: bool = Form(False),
    turbo: bool = Form(False),
    context: str = Form(""),
) -> JSONResponse:
    if model not in MODELS:
        raise HTTPException(400, f"Unknown model '{model}'")
    if language not in LANGUAGES:
        raise HTTPException(400, f"Unsupported language '{language}'")

    # The hint is prepended to every chunk's prompt, so its cost is paid once
    # per chunk for the whole file. Same limit as the stored setting.
    context = context.strip()
    if len(context) > MAX_CONTEXT_CHARS:
        raise HTTPException(
            400,
            f"Vocabulary hints are limited to {MAX_CONTEXT_CHARS} characters "
            f"(received {len(context)})",
        )

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            400, f"Unsupported file type '{suffix or 'none'}'. "
                 f"Supported: {', '.join(sorted(ALLOWED_SUFFIXES))}"
        )

    if suffix != ".wav" and shutil.which("ffmpeg") is None:
        raise HTTPException(
            400, "ffmpeg is required for non-WAV files. Install it with Homebrew "
                 "(brew install ffmpeg) or MacPorts (sudo port install ffmpeg)."
        )

    # Starlette already knows the size of a fully parsed UploadFile. Avoid a
    # second multi-gigabyte copy when that value is over the application limit;
    # the streaming counter remains authoritative when size is unavailable.
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, OVERSIZE_DETAIL)

    job_id = uuid.uuid4().hex[:12]
    dest = UPLOAD_DIR / f"{job_id}{suffix}"

    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(8 * 1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, OVERSIZE_DETAIL)
                out.write(chunk)
    except BaseException:
        # Includes the client disconnecting mid-upload, which raises a
        # cancellation that is not an Exception.
        dest.unlink(missing_ok=True)
        raise

    submit_error: BaseException | None = None
    future: Future | None = None
    with jobs_lock:
        _prune_jobs_locked()
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "detail": "Queued",
            "progress": 0.0,
            "filename": file.filename,
            "size": size,
            "model": model,
            "language": language,
            "timestamps": bool(timestamps),
            "turbo": bool(turbo),
            "partial": None,
            "context": context,
            "path": str(dest),
            "cancelled": threading.Event(),
            "created_at": time.time(),
            "result": None,
        }
        # Holding jobs_lock prevents a very fast worker from finishing before
        # its Future is registered for shutdown cleanup.
        try:
            future = executor.submit(_run_job, job_id)
            job_futures[job_id] = future
        except BaseException as exc:
            jobs.pop(job_id, None)
            submit_error = exc

    if submit_error is not None:
        dest.unlink(missing_ok=True)
        if isinstance(submit_error, RuntimeError):
            raise HTTPException(503, "The transcription worker is stopping") from submit_error
        raise submit_error

    assert future is not None
    future.add_done_callback(lambda _future: _forget_future(job_id))
    return JSONResponse({"id": job_id})


@app.get("/api/jobs")
def list_jobs() -> dict:
    """Everything the queue view needs, newest first.

    One worker means a second upload really does wait, so the queue has to be
    visible: without it a queued file looks indistinguishable from a hung one.
    """
    with jobs_lock:
        _prune_jobs_locked()
        ordered = sorted(
            jobs.values(), key=lambda job: job.get("created_at") or 0.0, reverse=True
        )
        return {"jobs": [_public_job(job) for job in ordered]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return _public_job(job)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        if job["status"] in TERMINAL_JOB_STATUSES:
            raise HTTPException(409, f"This job already finished ({job['status']})")
        job["cancelled"].set()
        # A queued job is never picked up, so settle it here rather than wait
        # for a worker that may be several long files away from reaching it.
        if job["status"] == "queued":
            job.update(status="cancelled", detail="Cancelled", finished_at=time.time())
            Path(job["path"]).unlink(missing_ok=True)
            return {"id": job_id, "status": "cancelled"}
        # A running job is a different answer. The model call cannot be
        # interrupted, so the worker only notices at the next chunk boundary —
        # minutes away on a long file. Saying "cancelled" while the job is
        # visibly still processing reads as a bug; say what is really true and
        # let the queue show it.
        job.update(cancel_requested=True,
                   detail="Cancelling — finishing the current chunk")
    return {"id": job_id, "status": "cancelling"}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> JSONResponse:
    """Requeue a failed job without a second upload.

    Only a failure qualifies. Cancelling is a deliberate "I don't want this",
    and its upload is deleted immediately rather than kept on the chance of a
    retry — this app does not hoard audio the user has already abandoned.
    """
    new_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        source = jobs.get(job_id)
        if source is None:
            raise HTTPException(404, "Job not found")
        if source["status"] != "error":
            raise HTTPException(409, "Only a failed job can be retried")
        pending = source.get("retried_as")
        if pending is not None and (pending in jobs or pending in retries_staging):
            # One worker and one GPU: a double-clicked Retry would otherwise
            # stage a second copy of the upload and transcribe it twice. Once
            # the retry is forgotten, this job may be retried afresh.
            raise HTTPException(409, "This job has already been retried")
        original = Path(source["path"])
        if not original.is_file():
            raise HTTPException(
                404, "The uploaded file is no longer available — upload it again"
            )
        # Claim the retry before releasing the lock, so a second request that
        # arrives during the copy below is rejected rather than duplicated.
        source["retried_as"] = new_id
        retries_staging.add(new_id)
        inherited = {key: value for key, value in source.items()
                     if key not in _RESET_ON_RETRY}

    destination = UPLOAD_DIR / f"{new_id}{original.suffix}"
    try:
        # Deliberately outside jobs_lock: the upload can be 4 GB, and the
        # running job's progress, the queue view, new uploads, and shutdown all
        # wait on that lock. copyfile rather than copy2 so the staged copy gets
        # its own mtime — inheriting the original's would expose it to the
        # startup sweep that deletes uploads older than a day.
        shutil.copyfile(original, destination)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        with jobs_lock:
            _release_retry_claim(job_id, new_id)
        raise HTTPException(500, f"Could not stage the file for a retry: {exc}") from exc

    submit_error: BaseException | None = None
    future: Future | None = None
    with jobs_lock:
        _prune_jobs_locked()
        jobs[new_id] = {
            **inherited,
            "id": new_id,
            "status": "queued",
            "detail": "Queued",
            "progress": 0.0,
            "partial": None,
            "result": None,
            "path": str(destination),
            "cancelled": threading.Event(),
            "created_at": time.time(),
        }
        # Same ordering as create_job: register the Future under the lock so
        # shutdown cleanup cannot miss a worker that starts immediately.
        try:
            future = executor.submit(_run_job, new_id)
            job_futures[new_id] = future
        except BaseException as exc:
            jobs.pop(new_id, None)
            _release_retry_claim(job_id, new_id)
            submit_error = exc
        else:
            # Registered in `jobs` now, which is what the next Retry collides
            # with; the claim has done its job.
            retries_staging.discard(new_id)

    if submit_error is not None:
        destination.unlink(missing_ok=True)
        if isinstance(submit_error, RuntimeError):
            raise HTTPException(503, "The transcription worker is stopping") from submit_error
        raise submit_error

    assert future is not None
    future.add_done_callback(lambda _future: _forget_future(new_id))
    return JSONResponse({"id": new_id})


@app.get("/api/transcripts")
def list_transcripts(q: str = "") -> dict:
    # Every term must appear in the filename or the full text (not just the
    # 220-character preview), so "budget q3" finds the meeting either way.
    terms = [_searchable(term) for term in q.split()] if q else []
    transcripts = []
    for path in TRANSCRIPTS_DIR.glob("*.json"):
        try:
            transcript = _read_transcript(path)
            summary = _transcript_summary(transcript)
        except Exception:
            # A damaged or partially copied file should not make the entire
            # history inaccessible.
            continue
        if terms:
            result = transcript.get("result")
            text = result.get("text") if isinstance(result, dict) else ""
            haystack = _searchable(
                f"{summary['filename']} {text if isinstance(text, str) else ''}"
            )
            if not all(term in haystack for term in terms):
                continue
        transcripts.append(summary)
    transcripts.sort(key=lambda item: item["finished_at"] or 0, reverse=True)
    return {"transcripts": transcripts}


def _export_member_name(transcript: dict, used: set[str]) -> str:
    """A readable, filesystem-safe member name that cannot collide."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", str(transcript.get("filename") or "Untitled"))
    stem = re.sub(r"[^\w \-.]", "_", stem).strip() or "Untitled"
    finished_at = _timestamp(transcript.get("finished_at"))
    if finished_at is not None:
        stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(finished_at))
        name = f"{stamp} — {stem}"
    else:
        name = stem
    if name in used:
        name = f"{name} — {transcript.get('id')}"
    # The inner id can itself collide (hand-copied files); numbering is total.
    unique, counter = name, 2
    while unique in used:
        unique = f"{name} ({counter})"
        counter += 1
    used.add(unique)
    return unique


@app.get("/api/transcripts/export")
def export_transcripts() -> Response:
    """Every saved transcript as one zip: plain text plus the full JSON."""
    entries = []
    for path in TRANSCRIPTS_DIR.glob("*.json"):
        try:
            transcript = _read_transcript(path)
        except Exception:
            continue
        if isinstance(transcript, dict):
            entries.append(transcript)
    if not entries:
        raise HTTPException(404, "There are no saved transcripts to export")
    entries.sort(key=lambda item: _timestamp(item.get("finished_at")) or 0)

    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "transcripts.json",
            json.dumps(entries, ensure_ascii=False, indent=2, allow_nan=False),
        )
        for transcript in entries:
            result = transcript.get("result")
            text = result.get("text") if isinstance(result, dict) else ""
            member = _export_member_name(transcript, used_names)
            archive.writestr(
                f"text/{member}.txt",
                text if isinstance(text, str) else "",
            )
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="qwen-scribe-transcripts.zip"'
        },
    )


@app.get("/api/transcripts/{transcript_id}")
def get_transcript(transcript_id: str) -> dict:
    path = _transcript_path(transcript_id)
    try:
        transcript = _read_transcript(path)
    except FileNotFoundError:
        raise HTTPException(404, "Transcript not found") from None
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"Could not read transcript: {exc}") from exc
    if not isinstance(transcript, dict):
        raise HTTPException(500, "Could not read transcript: it is not a JSON object")
    return transcript


@app.delete("/api/transcripts/{transcript_id}", status_code=204)
def delete_transcript(transcript_id: str) -> Response:
    path = _transcript_path(transcript_id)
    try:
        path.unlink()
    except FileNotFoundError:
        raise HTTPException(404, "Transcript not found") from None
    # Otherwise GET /api/jobs/{id} would keep serving the deleted text.
    _forget_jobs(transcript_id)
    return Response(status_code=204)


@app.delete("/api/transcripts")
def delete_all_transcripts() -> dict:
    deleted = 0
    removed_ids = []
    # *.json.tmp too: a crashed write must not leave an invisible transcript.
    for path in TRANSCRIPTS_DIR.glob("*.json*"):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted += 1
        removed_ids.append(path.name.split(".", 1)[0])
    _forget_jobs(*removed_ids)
    return {"deleted": deleted}


@app.post("/api/dictation/heartbeat")
def dictation_heartbeat(
    accessibility: bool | None = None,
    input_monitoring: bool | None = None,
    microphone: bool | None = None,
) -> dict:
    with dictation_lock:
        dictation_state.update(
            last_seen=time.time(),
            accessibility=accessibility,
            input_monitoring=input_monitoring,
            microphone=microphone,
        )
    return {"ok": True}


@app.get("/api/dictation/status")
def dictation_status() -> dict:
    with dictation_lock:
        state = dict(dictation_state)
    with settings_lock:
        dictation = dict(_settings["dictation"])
    return {
        "available": bool(state["last_seen"] and time.time() - state["last_seen"] < 30),
        "accessibility": state["accessibility"],
        "input_monitoring": state["input_monitoring"],
        "microphone": state["microphone"],
        "hotkey": dictation["hotkey"],
        "shortcut": DICTATION_HOTKEYS[dictation["hotkey"]],
        "model": dictation["model"],
        "language": dictation["language"],
    }


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


if __name__ == "__main__":
    print(f"\n  Qwen Scribe — open http://{HOST}:{PORT} in your browser\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
