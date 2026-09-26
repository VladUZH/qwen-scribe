"""The localhost HTTP API and the page that uses it."""

from __future__ import annotations

import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, decode, dictation, history, jobs, models, settings

TRANSCRIPT_NOT_FOUND = "Transcript not found"
UPLOAD_GONE = "The uploaded file is no longer available — upload it again"
WORKER_STOPPING = "The transcription worker is stopping"


@asynccontextmanager
async def lifespan(app: FastAPI):
    jobs.stopping.clear()
    # A conversion made by quantize_8bit.py next to server.py moves into the
    # store once, so it is offered rather than made again.
    models.adopt_legacy()
    models.sweep_partials()
    jobs.start_maintenance()
    # The first dictation of the day should not be the one that loads the
    # model. Only in the real server; see jobs.background_loading.
    jobs.warm_up(settings.current("dictation")["model"])
    yield
    jobs.stop_maintenance()
    jobs.shutdown()


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
    if config.HOST not in {"0.0.0.0", "::"}:
        allowed_hosts.add(config.HOST)

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
        limit = config.MAX_UPLOAD_BYTES + config.MULTIPART_OVERHEAD_BYTES
        if declared.isdigit() and int(declared) > limit:
            return _with_security_headers(
                JSONResponse({"detail": config.OVERSIZE_DETAIL}, status_code=413)
            )

    return _with_security_headers(await call_next(request))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    return {
        "models": list(config.MODELS.keys()),
        "default_model": config.DEFAULT_MODEL,
        "languages": config.LANGUAGES,
        "extensions": sorted(config.ALLOWED_SUFFIXES),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        # The app's own decoder covers everything but the five formats
        # AVFoundation will not read, so the page can say which.
        "decoder": decode.helper() is not None,
        "ffmpeg_formats": sorted(suffix.lstrip(".") for suffix in decode.FFMPEG_SUFFIXES),
    }


@app.get("/api/models")
def list_models() -> dict:
    return {
        "models": models.listing(converting=jobs.converting_models()),
        "default": config.DEFAULT_MODEL,
    }


@app.post("/api/models/{model_id}/prepare")
def prepare_model(model_id: str) -> JSONResponse:
    try:
        return JSONResponse({"id": jobs.prepare(model_id)})
    except jobs.JobNotFound:
        raise HTTPException(404, f"Unknown model '{model_id}'") from None
    except jobs.JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, WORKER_STOPPING) from exc


@app.delete("/api/models/{model_id}", status_code=204)
def remove_model(model_id: str) -> Response:
    if model_id not in config.MODEL_CATALOG:
        raise HTTPException(404, f"Unknown model '{model_id}'")
    if not models.is_quantized(model_id):
        raise HTTPException(409, f"{models.label(model_id)} is downloaded by the Hub library; there is nothing to remove here")
    if jobs.active_model_use(model_id):
        raise HTTPException(409, f"{models.label(model_id)} is in use by a job that has not finished")
    try:
        models.remove(model_id)
    except OSError as exc:
        raise HTTPException(500, f"Could not remove {models.label(model_id)}: {exc}") from exc
    return Response(status_code=204)


@app.get("/api/settings")
def get_settings() -> dict:
    return settings.response()


@app.put("/api/settings")
def update_settings(payload: dict) -> dict:
    before = settings.current("dictation")["model"]
    try:
        body = settings.update(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Could not save settings: {exc}") from exc
    # A changed dictation model is loaded now rather than on the next
    # dictation, for the same reason as the load at start. A variant that is
    # not prepared yet is skipped; the picker says so.
    if body["dictation"]["model"] != before and models.usable(body["dictation"]["model"]):
        jobs.warm_up(body["dictation"]["model"])
    return body


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    model: str = Form(config.DEFAULT_MODEL),
    language: str = Form("English"),
    timestamps: bool = Form(False),
    turbo: bool = Form(False),
    context: str = Form(""),
    source: str = Form("upload"),
) -> JSONResponse:
    if model not in config.MODELS:
        raise HTTPException(400, f"Unknown model '{model}'")
    if not models.usable(model):
        raise HTTPException(
            409, f"{models.label(model)} is not prepared yet. Prepare it from the model picker first."
        )
    if language not in config.LANGUAGES:
        raise HTTPException(400, f"Unsupported language '{language}'")
    if source not in jobs.SOURCES:
        raise HTTPException(400, f"Unknown source '{source}'")

    # The hint is prepended to every chunk's prompt, so its cost is paid once
    # per chunk for the whole file. Measured before stripping, so this and the
    # stored setting's validator accept exactly the same strings.
    if len(context) > config.MAX_CONTEXT_CHARS:
        raise HTTPException(
            400,
            f"Vocabulary hints are limited to {config.MAX_CONTEXT_CHARS} characters "
            f"(received {len(context)})",
        )
    context = context.strip()

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in config.ALLOWED_SUFFIXES:
        raise HTTPException(
            400, f"Unsupported file type '{suffix or 'none'}'. "
                 f"Supported: {', '.join(sorted(config.ALLOWED_SUFFIXES))}"
        )

    if decode.needs_ffmpeg(suffix) and shutil.which("ffmpeg") is None:
        raise HTTPException(400, decode.ffmpeg_required_message(suffix))

    # Starlette already knows the size of a fully parsed UploadFile. Avoid a
    # second multi-gigabyte copy when that value is over the application limit;
    # the streaming counter remains authoritative when size is unavailable.
    if file.size is not None and file.size > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, config.OVERSIZE_DETAIL)

    job_id = uuid.uuid4().hex[:12]
    dest = config.UPLOAD_DIR / f"{job_id}{suffix}"

    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(8 * 1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, config.OVERSIZE_DETAIL)
                out.write(chunk)
    except BaseException:
        # Includes the client disconnecting mid-upload, which raises a
        # cancellation that is not an Exception.
        dest.unlink(missing_ok=True)
        raise

    record = jobs.new_record(
        job_id, filename=file.filename, size=size, model=model, language=language,
        timestamps=timestamps, turbo=turbo, context=context, path=dest, source=source,
    )
    try:
        jobs.register(job_id, record)
    except RuntimeError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(503, WORKER_STOPPING) from exc
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return JSONResponse({"id": job_id})


@app.get("/api/jobs")
def list_jobs() -> dict:
    return {"jobs": jobs.listing()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return jobs.get_public(job_id)
    except jobs.JobNotFound:
        raise HTTPException(404, "Job not found") from None


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    try:
        return {"id": job_id, "status": jobs.cancel(job_id)}
    except jobs.JobNotFound:
        raise HTTPException(404, "Job not found") from None
    except jobs.JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> JSONResponse:
    try:
        return JSONResponse({"id": jobs.retry(job_id)})
    except jobs.JobNotFound:
        raise HTTPException(404, "Job not found") from None
    except jobs.UploadMissing:
        raise HTTPException(404, UPLOAD_GONE) from None
    except jobs.JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except jobs.StagingFailed as exc:
        raise HTTPException(500, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, WORKER_STOPPING) from exc


@app.get("/api/transcripts")
def list_transcripts(q: str = "") -> dict:
    return {"transcripts": history.list_summaries(q)}


@app.get("/api/transcripts/export")
def export_transcripts() -> Response:
    archive = history.export_archive()
    if archive is None:
        raise HTTPException(404, "There are no saved transcripts to export")
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="qwen-scribe-transcripts.zip"'
        },
    )


@app.get("/api/transcripts/{transcript_id}")
def get_transcript(transcript_id: str) -> dict:
    try:
        return history.read(transcript_id)
    except history.TranscriptNotFound:
        raise HTTPException(404, TRANSCRIPT_NOT_FOUND) from None
    except history.TranscriptUnreadable as exc:
        raise HTTPException(500, str(exc)) from exc


@app.delete("/api/transcripts/{transcript_id}", status_code=204)
def delete_transcript(transcript_id: str) -> Response:
    try:
        history.delete(transcript_id)
    except history.TranscriptNotFound:
        raise HTTPException(404, TRANSCRIPT_NOT_FOUND) from None
    # Otherwise GET /api/jobs/{id} would keep serving the deleted text.
    jobs._forget_jobs(transcript_id)
    return Response(status_code=204)


@app.delete("/api/transcripts")
def delete_all_transcripts() -> dict:
    deleted, removed_ids = history.delete_all()
    jobs._forget_jobs(*removed_ids)
    return {"deleted": deleted}


@app.post("/api/dictation/heartbeat")
def dictation_heartbeat(
    accessibility: bool | None = None,
    input_monitoring: bool | None = None,
    microphone: bool | None = None,
) -> dict:
    dictation.heartbeat(accessibility, input_monitoring, microphone)
    return {"ok": True}


@app.get("/api/dictation/status")
def dictation_status() -> dict:
    return dictation.status()


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
