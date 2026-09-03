"""Saved transcripts: readable JSON files, search, and export.

Hand-edited or truncated files reach every function here, so each field is
treated as untrusted: one bad file must never take the whole history down.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import time
import unicodedata
import zipfile
from pathlib import Path

from . import config


class TranscriptNotFound(LookupError):
    """No transcript with that id, or an id that is not even a valid one."""


class TranscriptUnreadable(ValueError):
    """The file exists but is not a readable transcript."""


def _transcript_path(transcript_id: str) -> Path:
    """Return a safe transcript path, rejecting path traversal and bad IDs."""
    if not re.fullmatch(r"[a-f0-9]{12}", transcript_id):
        raise TranscriptNotFound(transcript_id)
    return config.TRANSCRIPTS_DIR / f"{transcript_id}.json"


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


def _searchable(value: str) -> str:
    """Normalize text for user-facing, Unicode-aware transcript search."""
    return unicodedata.normalize("NFKC", value).casefold()


def list_summaries(query: str = "") -> list[dict]:
    """Summaries of every readable transcript, newest first.

    Every term must appear in the filename or the full text (not just the
    220-character preview), so "budget q3" finds the meeting either way.
    """
    terms = [_searchable(term) for term in query.split()] if query else []
    transcripts = []
    for path in config.TRANSCRIPTS_DIR.glob("*.json"):
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
    return transcripts


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


def export_archive() -> bytes | None:
    """Every saved transcript as one zip: plain text plus the full JSON.

    Returns None when there is nothing to export.
    """
    entries = []
    for path in config.TRANSCRIPTS_DIR.glob("*.json"):
        try:
            transcript = _read_transcript(path)
        except Exception:
            continue
        if isinstance(transcript, dict):
            entries.append(transcript)
    if not entries:
        return None
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
    return buffer.getvalue()


def read(transcript_id: str) -> dict:
    """The full stored transcript."""
    path = _transcript_path(transcript_id)
    try:
        transcript = _read_transcript(path)
    except FileNotFoundError:
        raise TranscriptNotFound(transcript_id) from None
    except (OSError, ValueError) as exc:
        raise TranscriptUnreadable(f"Could not read transcript: {exc}") from exc
    if not isinstance(transcript, dict):
        raise TranscriptUnreadable("Could not read transcript: it is not a JSON object")
    return transcript


def delete(transcript_id: str) -> None:
    path = _transcript_path(transcript_id)
    try:
        path.unlink()
    except FileNotFoundError:
        raise TranscriptNotFound(transcript_id) from None


def delete_all() -> tuple[int, list[str]]:
    """Remove every transcript file. Returns the count and the ids removed."""
    deleted = 0
    removed_ids = []
    # *.json.tmp too: a crashed write must not leave an invisible transcript.
    for path in config.TRANSCRIPTS_DIR.glob("*.json*"):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted += 1
        removed_ids.append(path.name.split(".", 1)[0])
    return deleted, removed_ids
