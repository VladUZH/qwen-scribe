"""The job store, the single GPU worker, and cancel, retry, and shutdown.

One worker: GPU work is serialized so parallel uploads queue instead of
thrashing unified memory. Everything the browser sees of a job comes from the
``jobs`` dict under ``jobs_lock``; the worker updates it as it goes.
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from . import cleanup, config, decode, history, models, sessions, settings

# Where a job came from. The page uploads files; the native helper sends its
# recordings as dictation, which is what lets the history opt-out apply to
# dictation alone.
SOURCES = {"upload", "dictation"}

# What a job does. A transcription has an upload behind it; a prepare job
# converts a catalog model on this Mac and has no file at all. Both go
# through the one worker, so a conversion queues behind a running
# transcription instead of competing with it for the GPU and the memory.
KINDS = {"transcribe", "prepare"}

# A dictation the user chose not to keep is remembered only long enough for
# the helper to collect the text, then forgotten along with the text.
EPHEMERAL_RETENTION_SECONDS = 60

# Loading a model in the background (at start, and when the dictation model
# changes) and releasing idle ones only happen in the real server. The entry
# point switches this on; the test suite never does, so no test loads a
# model it did not ask for.
background_loading = False
MAINTENANCE_INTERVAL_SECONDS = 60


class JobNotFound(LookupError):
    pass


class JobConflict(RuntimeError):
    """The job is in a state that does not allow the requested action."""


class UploadMissing(LookupError):
    """The staged upload a retry needs is gone; the file must be sent again."""


class StagingFailed(OSError):
    """The retry's copy of the upload could not be written."""


class _JobCancelled(Exception):
    """Raised inside the worker when the user cancels a running job."""


jobs: dict[str, dict] = {}
job_futures: dict[str, Future] = {}
jobs_lock = threading.Lock()

# Never sent to the browser: the staged upload path, a threading.Event that is
# not JSON-serialisable anyway, and the retry's id — the browser is given
# whether that retry is still outstanding instead, so its Retry button and this
# module's conflict answer can never disagree about it.
_PRIVATE_JOB_FIELDS = {"path", "cancelled", "retried_as"}

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

# One worker: serialize GPU work so parallel uploads queue instead of thrash.
executor = ThreadPoolExecutor(max_workers=1)

# Set on shutdown so a running transcription unwinds at the next chunk
# boundary instead of holding the process open for the rest of a long file.
stopping = threading.Event()


def _retry_outstanding(job: dict) -> bool:
    """Whether this job's retry is still around. Caller must hold jobs_lock.

    A retry that has since been forgotten leaves the job retryable again, so
    the answer has to be computed rather than stored.
    """
    pending = job.get("retried_as")
    return pending is not None and (pending in jobs or pending in retries_staging)


def _public_job(job: dict) -> dict:
    public = {key: value for key, value in job.items() if key not in _PRIVATE_JOB_FIELDS}
    public["retried"] = _retry_outstanding(job)
    return public


def _update(job_id: str, **fields) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        # Shutdown and normal completion are terminal ownership decisions. A
        # worker returning from an in-flight model call must never resurrect a
        # job that shutdown already marked as failed.
        if job is None or job.get("status") in config.TERMINAL_JOB_STATUSES:
            return
        # Once a cancel is pending, the worker's running commentary must not
        # paint over the one line telling the user it was accepted. It cannot
        # stop until the current step returns, and that step can be a
        # multi-minute ffmpeg decode.
        if job.get("cancel_requested") and fields.get("status") not in config.TERMINAL_JOB_STATUSES:
            fields.pop("detail", None)
        if fields.get("status") in config.TERMINAL_JOB_STATUSES:
            fields.setdefault("finished_at", time.time())
        job.update(fields)
        if fields.get("status") in config.TERMINAL_JOB_STATUSES:
            _prune_jobs_locked()


def _forget_future(job_id: str) -> None:
    with jobs_lock:
        job_futures.pop(job_id, None)


def _join_transcript_texts(texts: list[str], language: str | None) -> str:
    normalized = (language or "").strip().lower()
    return ("" if normalized in config.UNSPACED_LANGUAGE_ALIASES else " ").join(texts)


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
        if job.get("status") in config.TERMINAL_JOB_STATUSES
    ]
    evict = {job_id for stamp, job_id in finished if now - stamp > config.JOB_RETENTION_SECONDS}
    # A dictation kept out of history must not linger in memory for an hour
    # either; the helper collects it within seconds.
    evict.update(
        job_id for stamp, job_id in finished
        if jobs[job_id].get("ephemeral") and now - stamp > EPHEMERAL_RETENTION_SECONDS
    )
    surviving = sorted((item for item in finished if item[1] not in evict), reverse=True)
    evict.update(job_id for _stamp, job_id in surviving[config.MAX_REMEMBERED_JOBS:])
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
            if jobs.get(job_id, {}).get("status") in config.TERMINAL_JOB_STATUSES:
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

    if job.get("kind") == "prepare":
        _run_prepare(job_id, job)
        return

    path = Path(job["path"])
    cancelled = job["cancelled"]

    try:
        if cancelled.is_set():
            # Cancelled while it sat in the queue: never load a model for it.
            raise _JobCancelled
        if stopping.is_set():
            raise RuntimeError("Server stopped before this job started")
        _update(job_id, status="loading", detail="Loading model")
        # Loading can download several gigabytes of weights. A cancel that
        # arrived while this job was still queued must not pay for that.
        if cancelled.is_set():
            raise _JobCancelled

        def report_download(done: int, total: int) -> None:
            _update(
                job_id,
                detail=f"Downloading model · {done / 1e9:.1f} of {total / 1e9:.1f} GB",
                progress=(done / total) if total else 0.0,
            )

        # The first run fetches the weights; say how far along it is rather
        # than sitting on "loading" for the minutes a 3.4 GB download takes.
        sessions.ensure_downloaded(job["model"], progress=report_download)
        if cancelled.is_set():
            raise _JobCancelled
        _update(job_id, detail="Loading model", progress=0.0)
        session = sessions.get_session(job["model"])

        # Speculative decoding: the 0.6B model drafts tokens, the 1.7B verifies
        # them in parallel — more GPU utilization, same output quality.
        draft_model = None
        if job["turbo"] and models.base_of(job["model"]) == "1.7b":
            _update(job_id, detail="Loading 0.6B draft model for speculative decoding")
            # Pass the Session-owned model itself. Passing its string id makes
            # mlx-qwen3-asr load a second independent 0.6B copy into its global
            # model holder, wasting roughly another 1.2 GB of unified memory.
            draft_model = sessions.get_session("0.6b").model

        _update(job_id, detail="Decoding audio")
        from mlx_qwen3_asr.chunking import split_audio_into_chunks

        SR = decode.SAMPLE_RATE
        # A 16 kHz mono WAV is read here; other formats go through the app's
        # own decoder, and only what AVFoundation will not read needs ffmpeg.
        audio = decode.to_waveform(path)
        # Decoding a 4 GB video takes minutes and cannot be interrupted, so a
        # cancel that arrived during it has been waiting all that time. Check
        # before starting on the GPU rather than at the first chunk boundary.
        if cancelled.is_set():
            raise _JobCancelled
        if stopping.is_set():
            raise RuntimeError("Server stopped before this job finished")
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
                # Deliberately unguarded. If this fails too then the timestamps
                # were never the problem, and its failure — not the aligner's —
                # is the one that killed the job and the one worth reporting.
                result = session.transcribe(
                    chunk_audio, **{**kwargs, "return_timestamps": False}
                )
                # Stay timestamp-free for the rest of the file, so a two-hour
                # recording does not fail twice per chunk. The segments already
                # collected can no longer be used — a half-length .srt is worse
                # than none — so let a long file's worth of them go now rather
                # than carry them to the end.
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

        # Counter preserves first-seen order for ties, so the reported language
        # is the same between processes.
        language = Counter(languages).most_common(1)[0][0] if languages else None
        finished_at = time.time()
        text = _join_transcript_texts(texts, language)
        if job.get("source") == "dictation":
            # Spoken commands, the user's replacements, and tidy whitespace:
            # for text about to be pasted, and never for a file's transcript.
            text = cleanup.dictation_text(text, language, settings.current("dictation"))
        result = {
            "text": text,
            "language": language,
            # word-level [{text,start,end}]
            "segments": None if timestamps_unavailable else (segments or None),
            "truncated": truncated,
        }
        history_saved = True
        history_error = None
        # Read at the moment of saving, so a change made while a long
        # dictation was still transcribing applies to it.
        keep = (
            job.get("source") != "dictation"
            or settings.current("dictation").get("save_history", True)
        )
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
                or current.get("status") in config.TERMINAL_JOB_STATUSES
            ):
                raise RuntimeError("Server stopped before this job finished")
            if keep:
                try:
                    history._save_transcript(completed_job, result, finished_at)
                except Exception as exc:
                    # Never discard a successful transcription merely because
                    # its history file could not be written. The UI surfaces
                    # this warning.
                    history_saved = False
                    history_error = f"Could not save transcript: {type(exc).__name__}: {exc}"
            else:
                # By choice, not by failure: no warning, and the record is
                # evicted soon after the helper has collected it.
                history_saved = False
            current.update(
                status="done",
                progress=1.0,
                detail="Done",
                partial=None,
                finished_at=finished_at,
                result=result,
                history_saved=history_saved,
                history_error=history_error,
                ephemeral=not keep,
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



def _run_prepare(job_id: str, job: dict) -> None:
    """Convert a catalog model on the worker, reporting each step on the job."""
    cancelled = job["cancelled"]
    model_id = job["model"]
    try:
        if cancelled.is_set():
            raise _JobCancelled
        if stopping.is_set():
            raise RuntimeError("Server stopped before this job started")
        base = models.base_of(model_id)
        _update(job_id, status="loading", detail=f"Checking {models.label(base)} weights")

        def report_download(done: int, total: int) -> None:
            _update(
                job_id,
                detail=f"Downloading {models.label(base)} · {done / 1e9:.1f} of {total / 1e9:.1f} GB",
                progress=(done / total) if total else 0.0,
            )

        sessions.ensure_downloaded(base, progress=report_download)
        if cancelled.is_set():
            raise _JobCancelled
        if stopping.is_set():
            raise RuntimeError("Server stopped before this job finished")
        # The conversion holds a full fp16 copy of the model while it works.
        # Anything loaded for transcription would sit next to it; on an 8 GB
        # Mac that is the difference between finishing and swapping. The next
        # job reloads what it needs in a few seconds.
        if sessions.loaded_models():
            _update(job_id, detail="Releasing loaded models")
            sessions.drop_all()
        _update(job_id, status="processing", started_at=time.time(), progress=0.0)

        def report(detail: str) -> None:
            _update(job_id, detail=detail)

        models.convert(model_id, report=report, cancelled=cancelled)
        with jobs_lock:
            current = jobs.get(job_id)
            if cancelled.is_set():
                raise _JobCancelled
            if stopping.is_set() or current is None or current.get("status") in config.TERMINAL_JOB_STATUSES:
                raise RuntimeError("Server stopped before this job finished")
            current.update(
                status="done", progress=1.0, detail="Ready", finished_at=time.time(),
                result={"model": model_id, "path": models.source(model_id)},
            )
            _prune_jobs_locked()
    except (_JobCancelled, models.ConversionCancelled):
        _update(job_id, status="cancelled", detail="Cancelled", finished_at=time.time())
    except Exception as exc:
        if cancelled.is_set():
            _update(job_id, status="cancelled", detail="Cancelled", finished_at=time.time())
        else:
            _update(job_id, status="error", detail=f"{type(exc).__name__}: {exc}", finished_at=time.time())


# ---------------------------------------------------------------------------
# Operations the API exposes
# ---------------------------------------------------------------------------

def new_record(job_id: str, *, filename: str | None, size: int, model: str,
               language: str, timestamps: bool, turbo: bool, context: str,
               path: Path, source: str = "upload") -> dict:
    """A fresh queued job record for a staged upload."""
    return {
        "id": job_id,
        "kind": "transcribe",
        "status": "queued",
        "detail": "Queued",
        "progress": 0.0,
        "filename": filename,
        "size": size,
        "model": model,
        "language": language,
        "timestamps": bool(timestamps),
        "turbo": bool(turbo),
        "partial": None,
        "context": context,
        "source": source,
        "path": str(path),
        "cancelled": threading.Event(),
        "created_at": time.time(),
        "result": None,
    }


def register(job_id: str, record: dict) -> None:
    """Add a job to the store and hand it to the worker.

    Whatever the executor raises propagates after the record is removed
    again; the caller owns the staged file and removes it. A RuntimeError is
    the executor refusing work because it is shutting down.
    """
    with jobs_lock:
        _prune_jobs_locked()
        jobs[job_id] = record
        # Holding jobs_lock prevents a very fast worker from finishing before
        # its Future is registered for shutdown cleanup.
        try:
            future = executor.submit(_run_job, job_id)
            job_futures[job_id] = future
        except BaseException:
            jobs.pop(job_id, None)
            raise
    future.add_done_callback(lambda _future: _forget_future(job_id))


def converting_models() -> set[str]:
    """Catalog ids with a prepare job that has not finished."""
    with jobs_lock:
        return {
            job["model"] for job in jobs.values()
            if job.get("kind") == "prepare" and job.get("status") not in config.TERMINAL_JOB_STATUSES
        }


def prepare(model_id: str) -> str:
    """Queue the conversion of a catalog model; returns the job id.

    One conversion per model at a time: a second click while the first is
    queued or running answers with the conflict rather than a second job
    that would redo the work and race the first for the output directory.
    """
    if model_id not in config.MODEL_CATALOG:
        raise JobNotFound(model_id)
    if not models.is_quantized(model_id):
        raise JobConflict(f"{models.label(model_id)} is downloaded on first use; there is nothing to prepare")
    if models.converted(model_id):
        raise JobConflict(f"{models.label(model_id)} is already prepared")
    if model_id in converting_models():
        raise JobConflict(f"{models.label(model_id)} is already being prepared")
    job_id = uuid.uuid4().hex[:12]
    record = {
        "id": job_id,
        "kind": "prepare",
        "status": "queued",
        "detail": "Queued",
        "progress": 0.0,
        "filename": None,
        "model": model_id,
        "label": models.label(model_id),
        "source": "prepare",
        "partial": None,
        "cancelled": threading.Event(),
        "created_at": time.time(),
        "result": None,
    }
    register(job_id, record)
    return job_id


def active_model_use(model_id: str) -> bool:
    """Whether an unfinished job needs this model, so it must not be removed."""
    with jobs_lock:
        return any(
            job.get("model") == model_id and job.get("status") not in config.TERMINAL_JOB_STATUSES
            for job in jobs.values()
        )


def listing() -> list[dict]:
    """Everything the queue view needs, newest first.

    One worker means a second upload really does wait, so the queue has to be
    visible: without it a queued file looks indistinguishable from a hung one.
    """
    with jobs_lock:
        _prune_jobs_locked()
        ordered = sorted(
            jobs.values(), key=lambda job: job.get("created_at") or 0.0, reverse=True
        )
        return [_public_job(job) for job in ordered]


def get_public(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return _public_job(job)


def cancel(job_id: str) -> str:
    """Cancel a job. Returns "cancelled" or "cancelling".

    A queued job is never picked up, so it is settled here rather than waiting
    for a worker that may be several long files away from reaching it. A
    running job is a different answer: the model call cannot be interrupted,
    so the worker only notices at the next chunk boundary. Saying "cancelled"
    while the job is visibly still processing reads as a bug, so the truth is
    reported and the queue shows it.
    """
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        if job["status"] in config.TERMINAL_JOB_STATUSES:
            raise JobConflict(f"This job already finished ({job['status']})")
        job["cancelled"].set()
        if job["status"] == "queued":
            job.update(status="cancelled", detail="Cancelled", finished_at=time.time())
            if job.get("path"):
                Path(job["path"]).unlink(missing_ok=True)
            return "cancelled"
        job.update(cancel_requested=True,
                   detail="Cancelling — finishing the current chunk")
    return "cancelling"


def retry(job_id: str) -> str:
    """Requeue a failed job without a second upload; returns the new id.

    Only a failure qualifies. Cancelling is a deliberate "I don't want this",
    and its upload is deleted immediately rather than kept on the chance of a
    retry — this app does not hoard audio the user has already abandoned.
    """
    new_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        source = jobs.get(job_id)
        if source is None:
            raise JobNotFound(job_id)
        if source["status"] != "error":
            raise JobConflict("Only a failed job can be retried")
        if source.get("kind") == "prepare":
            raise JobConflict("Prepare the model again from the model picker")
        if _retry_outstanding(source):
            # One worker and one GPU: a double-clicked Retry would otherwise
            # stage a second copy of the upload and transcribe it twice. Once
            # the retry is forgotten, this job may be retried afresh.
            raise JobConflict("This job has already been retried")
        original = Path(source["path"])
        if not original.is_file():
            raise UploadMissing(job_id)
        # Claim the retry before releasing the lock, so a second request that
        # arrives during the copy below is rejected rather than duplicated.
        source["retried_as"] = new_id
        retries_staging.add(new_id)
        inherited = {key: value for key, value in source.items()
                     if key not in _RESET_ON_RETRY}

    destination = config.UPLOAD_DIR / f"{new_id}{original.suffix}"
    registered = False
    try:
        try:
            # Deliberately outside jobs_lock: the upload can be 4 GB, and the
            # running job's progress, the queue view, new uploads, and shutdown
            # all wait on that lock. copyfile rather than copy2 because the
            # staged copy wants its own mtime, not the original's.
            shutil.copyfile(original, destination)
        except FileNotFoundError as exc:
            # The source job was forgotten between the check above and here,
            # taking its upload with it. Same situation, same answer.
            raise UploadMissing(job_id) from exc
        except OSError as exc:
            raise StagingFailed(f"Could not stage the file for a retry: {exc}") from exc

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
            # Same ordering as register: the Future goes in under the lock so
            # shutdown cleanup cannot miss a worker that starts immediately.
            try:
                future = executor.submit(_run_job, new_id)
                job_futures[new_id] = future
            except BaseException:
                jobs.pop(new_id, None)
                raise
            # In `jobs` now, which is what the next Retry collides with.
            registered = True
            retries_staging.discard(new_id)
    finally:
        # Anything that got here without registering — a failed copy, a refused
        # submission, an error from pruning — must give the claim back. Leaving
        # it would make this job permanently un-retryable and strand the copy.
        if not registered:
            destination.unlink(missing_ok=True)
            with jobs_lock:
                _release_retry_claim(job_id, new_id)

    future.add_done_callback(lambda _future: _forget_future(new_id))
    return new_id


# ---------------------------------------------------------------------------
# Background loading and unloading
# ---------------------------------------------------------------------------

_warming = 0
_maintenance_stop = threading.Event()
_maintenance_thread: threading.Thread | None = None


def warm_up(model_key: str) -> Future | None:
    """Load a model on the worker without a visible job.

    Used for the dictation model at start and whenever it changes, so the
    first dictation is not the one that pays for loading. Queues behind any
    file already being transcribed, since the worker is the one GPU lane.
    """
    if not background_loading:
        return None
    if not settings.current("performance").get("preload_dictation_model", True):
        return None
    try:
        return executor.submit(_warm, model_key)
    except RuntimeError:
        return None   # the executor is shutting down


def _warm(model_key: str) -> None:
    global _warming
    _warming += 1
    try:
        if stopping.is_set():
            return
        if not models.usable(model_key):
            # A variant chosen but not yet prepared; the picker says so.
            return
        sessions.ensure_downloaded(model_key)
        if stopping.is_set():
            return
        sessions.get_session(model_key)
    except Exception as exc:
        # Nothing to attach the failure to; the next real job will report it.
        print(f"Background model load failed: {type(exc).__name__}: {exc}")
    finally:
        _warming -= 1


def unload_idle_sessions(now: float | None = None) -> list[str]:
    """Release models unused for the configured time; never during work."""
    minutes = settings.current("performance").get("unload_after_minutes", 0)
    if not minutes:
        return []
    with jobs_lock:
        busy = any(job.get("status") in {"loading", "processing"} for job in jobs.values())
    if busy or _warming:
        return []
    return sessions.drop_idle(minutes * 60, now=now)


def start_maintenance() -> None:
    """Start the once-a-minute idle check. Idempotent."""
    global _maintenance_thread
    if not background_loading:
        return
    if _maintenance_thread is not None and _maintenance_thread.is_alive():
        return
    _maintenance_stop.clear()

    def loop() -> None:
        while not _maintenance_stop.wait(MAINTENANCE_INTERVAL_SECONDS):
            try:
                unload_idle_sessions()
            except Exception as exc:
                print(f"Idle model check failed: {type(exc).__name__}: {exc}")

    _maintenance_thread = threading.Thread(target=loop, name="qwen-scribe-maintenance", daemon=True)
    _maintenance_thread.start()


def stop_maintenance() -> None:
    _maintenance_stop.set()


def shutdown() -> None:
    """Settle every unfinished job and remove every staged upload.

    Runs from the application's lifespan on the way out. Without it, Python's
    atexit handler joins the worker thread and silently runs every queued job
    to completion before the process exits. Unfinished jobs are marked while
    holding the same lock the worker uses for its final transcript write, so
    shutdown and successful persistence are a single ownership decision.
    """
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
        # Every staged upload dies with the process, so remove them all here
        # rather than trusting anything that runs later. The worker's own
        # cleanup in _run_job never gets its turn on a real quit: uvicorn
        # re-raises the SIGTERM it caught as soon as the lifespan returns, and
        # the process is gone before the thread comes back from its model
        # call. A failed job's copy, kept so Retry can reuse it, is dead too:
        # the job it could be retried from lives only in this process.
        staged = [Path(job["path"]) for job in jobs.values() if job.get("path")]
        pending = list(job_futures.values())
    # Work that never entered _run_job is dropped here; cancel_futures below
    # would do the same, but cancelling first keeps the done-callbacks tidy.
    for future in pending:
        future.cancel()
    for path in staged:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    executor.shutdown(wait=False, cancel_futures=True)
