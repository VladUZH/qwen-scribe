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

UPLOAD_DIR = Path(tempfile.gettempdir()) / "qwen-scribe-uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# A hard crash can bypass a job's finally block. Remove only old files so a
# second accidental server process cannot interfere with an active upload.
for stale_upload in UPLOAD_DIR.iterdir():
    try:
        if stale_upload.is_file() and time.time() - stale_upload.stat().st_mtime > 24 * 60 * 60:
            stale_upload.unlink()
    except OSError:
        pass

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
    temporary_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _read_transcript(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _transcript_summary(transcript: dict) -> dict:
    result = transcript.get("result") or {}
    text = result.get("text") or ""
    normalized = " ".join(text.split())
    started_at = transcript.get("started_at")
    finished_at = transcript.get("finished_at")
    duration_seconds = None
    if isinstance(started_at, (int, float)) and isinstance(finished_at, (int, float)):
        duration_seconds = max(0, finished_at - started_at)
    return {
        "id": transcript.get("id"),
        "filename": transcript.get("filename") or "Untitled recording",
        "created_at": transcript.get("created_at"),
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

app = FastAPI(title="Qwen Scribe", docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_requests_only(request: Request, call_next):
    """Reject DNS rebinding and cross-site browser requests to the local API."""
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
    if HOST not in {"0.0.0.0", "::"}:
        allowed_hosts.add(HOST)

    if (request.url.hostname or "").lower() not in allowed_hosts:
        return JSONResponse({"detail": "Untrusted Host header"}, status_code=400)

    origin = request.headers.get("origin")
    if origin:
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin.rstrip("/") != expected_origin.rstrip("/"):
            return JSONResponse(
                {"detail": "Cross-origin requests are not allowed"},
                status_code=403,
            )

    response = await call_next(request)
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/config")
def config() -> dict:
    return {
        "models": list(MODELS.keys()),
        "default_model": DEFAULT_MODEL,
        "languages": LANGUAGES,
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
            400, "ffmpeg is required for non-WAV files. Install it with: brew install ffmpeg"
        )

    job_id = uuid.uuid4().hex[:12]
    dest = UPLOAD_DIR / f"{job_id}{suffix}"

    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(8 * 1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "File exceeds the 4 GB upload limit")
            out.write(chunk)

    with jobs_lock:
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
        except (OSError, ValueError, TypeError):
            # A damaged or partially copied file should not make the entire
            # history inaccessible.
            continue
    transcripts.sort(key=lambda item: item.get("finished_at") or 0, reverse=True)
    return {"transcripts": transcripts}


@app.get("/api/transcripts/{transcript_id}")
def get_transcript(transcript_id: str) -> dict:
    path = _transcript_path(transcript_id)
    if not path.exists():
        raise HTTPException(404, "Transcript not found")
    try:
        return _read_transcript(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"Could not read transcript: {exc}") from exc


@app.delete("/api/transcripts/{transcript_id}", status_code=204)
def delete_transcript(transcript_id: str) -> Response:
    path = _transcript_path(transcript_id)
    if not path.exists():
        raise HTTPException(404, "Transcript not found")
    path.unlink()
    return Response(status_code=204)


@app.delete("/api/transcripts")
def delete_all_transcripts() -> dict:
    deleted = 0
    for path in TRANSCRIPTS_DIR.glob("*.json"):
        try:
            path.unlink()
            deleted += 1
        except FileNotFoundError:
            pass
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
