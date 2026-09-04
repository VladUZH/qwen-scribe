"""From a media file to the waveform the model wants, asking as little of the
Mac as possible.

Three ways, in this order:

* A 16 kHz mono 16-bit WAV is read here, with nothing but the standard
  library. That is what dictation sends and what the decoder below produces,
  so the common paths need no external program at all.
* Anything else that AVFoundation can read goes through the app's own helper
  (``--decode``, found through ``QWEN_SCRIBE_DECODER``). This is why a Mac
  with no Homebrew can still transcribe an ``.m4a`` or an ``.mp4``.
* What AVFoundation will not read — Matroska, WebM, Ogg, Opus, WMA — still
  needs ffmpeg, and the library's own loader handles those.

Everything returns exactly what ``mlx_qwen3_asr.audio.load_audio_np`` would:
mono float32 at 16 kHz.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from . import config

SAMPLE_RATE = 16000

# Formats the helper can decode on macOS 14, which is every one the app
# accepts except the five below. Kept as suffixes rather than probing the
# file, because the answer decides which error a refused upload gets.
HELPER_SUFFIXES = frozenset({
    ".wav", ".aiff", ".mp3", ".m4a", ".aac", ".qta", ".flac",
    ".mp4", ".mov", ".m4v",
})
# What AVFoundation does not read, so ffmpeg stays the only way.
FFMPEG_SUFFIXES = frozenset({".mkv", ".webm", ".ogg", ".opus", ".wma", ".avi"})

FFMPEG_INSTALL = ("Install it with Homebrew (brew install ffmpeg) or MacPorts "
                  "(sudo port install ffmpeg).")


class DecodeError(RuntimeError):
    """The audio could not be turned into a waveform, with the reason why."""


def helper() -> str | None:
    """The app's decoder, when the launcher told us where it is."""
    path = getattr(config, "DECODER", "") or ""
    return path if path and Path(path).is_file() else None


def plan(suffix: str) -> str:
    """Which of the three ways would be used for this suffix."""
    suffix = suffix.lower()
    if suffix == ".wav":
        return "wave"
    if suffix in HELPER_SUFFIXES and helper() is not None:
        return "helper"
    return "ffmpeg"


def needs_ffmpeg(suffix: str) -> bool:
    """Whether this file cannot be read without ffmpeg on this machine.

    A WAV never needs it — but a WAV that is not already 16 kHz mono does,
    which is only discovered on reading, so the upload is accepted and the
    job reports it. Refusing every WAV on a Mac without ffmpeg would turn
    away the files that work.
    """
    return suffix.lower() != ".wav" and plan(suffix) == "ffmpeg"


def ffmpeg_required_message(suffix: str) -> str:
    if suffix.lower() in FFMPEG_SUFFIXES:
        formats = ", ".join(sorted(s.lstrip(".") for s in FFMPEG_SUFFIXES))
        return (f"{formats} still need ffmpeg; everything else the app decodes "
                f"itself. {FFMPEG_INSTALL}")
    return (f"ffmpeg is required for {suffix} files when the app's own decoder "
            f"is unavailable, as it is outside the Mac app. {FFMPEG_INSTALL}")


def read_wave(path: Path):
    """A 16 kHz mono 16-bit WAV as mono float32, or None if it is not one.

    None rather than an exception: anything else is a job for a decoder, and
    which one depends on the file.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            if (handle.getnchannels() != 1 or handle.getsampwidth() != 2
                    or handle.getframerate() != SAMPLE_RATE):
                return None
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error, EOFError):
        return None
    pcm = np.frombuffer(frames, dtype="<i2")
    # The same scaling the library applies to integer PCM.
    return (pcm.astype(np.float32) / 32768.0).copy()


def _decode_with_helper(path: Path, tool: str):
    """Run the app's decoder into a temporary WAV and read that."""
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=config.UPLOAD_DIR, prefix="decoded-", suffix=".wav", delete=False)
    handle.close()
    decoded = Path(handle.name)
    try:
        finished = subprocess.run(
            [tool, "--decode", str(path), str(decoded)],
            capture_output=True, text=True, timeout=config.DECODE_TIMEOUT_SECONDS,
        )
        if finished.returncode != 0:
            reason = (finished.stderr or finished.stdout or "").strip().splitlines()
            raise DecodeError(reason[-1] if reason else
                              f"the app's decoder failed ({finished.returncode})")
        waveform = read_wave(decoded)
        if waveform is None:
            raise DecodeError("the app's decoder produced audio in an unexpected format")
        return waveform
    except subprocess.TimeoutExpired as exc:
        raise DecodeError("the app's decoder took too long and was stopped") from exc
    finally:
        decoded.unlink(missing_ok=True)


def _decode_with_ffmpeg(path: Path):
    """The library's own loader, which shells out to ffmpeg.

    Asked to decode before checking whether ffmpeg exists: the library is the
    authority on what it can read, and a missing ffmpeg is only worth a
    friendlier message than the one it raises.
    """
    from mlx_qwen3_asr.audio import load_audio_np

    try:
        return load_audio_np(str(path), sr=SAMPLE_RATE)
    except Exception as exc:                     # the library raises RuntimeError
        if shutil.which("ffmpeg") is None:
            raise DecodeError(ffmpeg_required_message(path.suffix)) from exc
        raise DecodeError(f"ffmpeg could not decode this file: {exc}") from exc


def to_waveform(path: Path):
    """Mono float32 at 16 kHz, by whichever route this file needs."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        waveform = read_wave(path)
        if waveform is not None:
            return waveform
        # A WAV at another rate, or with two channels: still needs a decoder.

    tool = helper()
    if tool is not None and path.suffix.lower() in HELPER_SUFFIXES:
        try:
            return _decode_with_helper(path, tool)
        except DecodeError as exc:
            # The helper is the fast path, not the only one. A file it cannot
            # read — an exotic codec in a container it does know — is worth
            # trying with ffmpeg before giving up on it.
            if shutil.which("ffmpeg") is None:
                raise DecodeError(f"{exc}, and ffmpeg is not installed to try instead. "
                                  f"{FFMPEG_INSTALL}") from exc
            return _decode_with_ffmpeg(path)

    return _decode_with_ffmpeg(path)
