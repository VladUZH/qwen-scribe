"""
Qwen Scribe — local transcription server for Apple Silicon.

FastAPI wrapper around mlx-qwen3-asr (Qwen3-ASR running natively on Metal).
Upload audio or video in the browser at http://localhost:8990 — nothing
leaves your machine.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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

# Languages exposed in the UI. "auto" lets the model detect the language.
LANGUAGES = ["auto", "English", "German", "Russian", "French", "Italian", "Spanish"]

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
OVERSIZE_DETAIL = "File exceeds the 4 GB upload limit"

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
jobs_lock = threading.Lock()

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

# One worker: serialize GPU work so parallel uploads queue instead of thrash.
executor = ThreadPoolExecutor(max_workers=1)

# Set on shutdown so a running transcription unwinds at the next chunk
# boundary instead of holding the process open for the rest of a long file.
stopping = threading.Event()

# Finished jobs are kept only long enough for the browser to collect the
# result; the transcript itself lives in TRANSCRIPTS_DIR.
JOB_RETENTION_SECONDS = 60 * 60
MAX_REMEMBERED_JOBS = 50


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


def _read_transcript(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp(value: object) -> float | None:
    """Return value as a float only if it really is a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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
        if job_id in jobs:
            jobs[job_id].update(fields)


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
        if job.get("status") in {"done", "error"}
    ]
    evict = {job_id for stamp, job_id in finished if now - stamp > JOB_RETENTION_SECONDS}
    surviving = sorted((item for item in finished if item[1] not in evict), reverse=True)
    evict.update(job_id for _stamp, job_id in surviving[MAX_REMEMBERED_JOBS:])
    for job_id in evict:
        jobs.pop(job_id, None)


def _forget_jobs(*job_ids: str) -> None:
    """Drop finished jobs whose transcript the user just deleted."""
    with jobs_lock:
        for job_id in job_ids:
            if jobs.get(job_id, {}).get("status") in {"done", "error"}:
                del jobs[job_id]


def _run_job(job_id: str) -> None:
    with jobs_lock:
        job = dict(jobs[job_id])

    path = Path(job["path"])
    try:
        _update(job_id, status="loading", detail="Loading model (first run downloads weights)")
        session = get_session(job["model"])

        # Speculative decoding: the 0.6B model drafts tokens, the 1.7B verifies
        # them in parallel — more GPU utilization, same output quality.
        draft_model = None
        if job["turbo"] and job["model"] == "1.7b":
            _update(job_id, detail="Loading 0.6B draft model for speculative decoding")
            get_session("0.6b")  # ensure weights are downloaded/cached
            draft_model = MODELS["0.6b"]

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
        if draft_model:
            kwargs["draft_model"] = draft_model

        texts: list[str] = []
        segments: list[dict] = []
        languages: list[str] = []
        truncated = False
        processed_sec = 0.0

        # split_audio_into_chunks returns (waveform, offset_seconds) tuples.
        for i, (chunk_audio, chunk_offset) in enumerate(chunks):
            if stopping.is_set():
                raise RuntimeError("Server stopped before this job finished")
            result = session.transcribe(chunk_audio, **kwargs)
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
                partial=" ".join(texts),
            )

        language = max(set(languages), key=languages.count) if languages else None
        finished_at = time.time()
        result = {
            "text": " ".join(texts),
            "language": language,
            "segments": segments or None,   # word-level [{text,start,end}]
            "truncated": truncated,
        }
        history_saved = True
        history_error = None
        completed_job = {**job, "started_at": started_at}
        try:
            _save_transcript(completed_job, result, finished_at)
        except Exception as exc:
            # Never discard a successful transcription merely because its
            # history file could not be written. The UI surfaces this warning.
            history_saved = False
            history_error = f"Could not save transcript: {type(exc).__name__}: {exc}"
        _update(
            job_id,
            status="done",
            progress=1.0,
            detail="Done",
            partial=None,
            finished_at=finished_at,
            result=result,
            history_saved=history_saved,
            history_error=history_error,
        )
    except Exception as exc:  # surface the real cause to the UI
        _update(job_id, status="error", detail=f"{type(exc).__name__}: {exc}")
    finally:
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
    stopping.set()
    executor.shutdown(wait=False, cancel_futures=True)
    with jobs_lock:
        for job in jobs.values():
            if job.get("status") in {"queued", "loading", "processing"}:
                job.update(status="error", detail="Server stopped before this job finished")


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
            "context": context.strip(),
            "path": str(dest),
            "created_at": time.time(),
            "result": None,
        }

    executor.submit(_run_job, job_id)
    return JSONResponse({"id": job_id})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        public = {k: v for k, v in job.items() if k != "path"}
    return public


@app.get("/api/transcripts")
def list_transcripts() -> dict:
    transcripts = []
    for path in TRANSCRIPTS_DIR.glob("*.json"):
        try:
            transcripts.append(_transcript_summary(_read_transcript(path)))
        except Exception:
            # A damaged or partially copied file should not make the entire
            # history inaccessible.
            continue
    transcripts.sort(key=lambda item: item["finished_at"] or 0, reverse=True)
    return {"transcripts": transcripts}


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
    return {
        "available": bool(state["last_seen"] and time.time() - state["last_seen"] < 30),
        "accessibility": state["accessibility"],
        "input_monitoring": state["input_monitoring"],
        "microphone": state["microphone"],
        "shortcut": "Right Command",
        "model": "1.7b",
        "language": "auto",
    }


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


if __name__ == "__main__":
    print(f"\n  Qwen Scribe — open http://{HOST}:{PORT} in your browser\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
