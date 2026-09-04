"""Paths, limits, and the tables the interface is built from.

Other modules read these through the module (``config.UPLOAD_DIR``) rather
than importing the names, so a test can point one at a temporary directory
and every user of it follows.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

# Deliberately fixed: this API has no remote-user authentication and must not
# be exposed to a LAN or the public internet.
HOST = "127.0.0.1"
PORT = int(os.environ.get("QWEN_SCRIBE_PORT", "8990"))

# The directory that holds server.py, static/, and this package, both in the
# repository and in the app's private runtime copy.
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

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

# Where the models the app converts on this Mac are stored, one directory per
# catalog entry. In the app data directory rather than next to the source,
# so an app upgrade, which replaces the runtime copy, keeps them. Before the
# catalog, quantize_8bit.py wrote into ./models next to server.py; the
# server adopts such a directory on start (models.adopt_legacy).
MODEL_DIR = Path(os.environ.get("QWEN_SCRIBE_MODEL_DIR", APP_DATA_DIR / "models")).expanduser()
LEGACY_MODEL_DIR = BASE_DIR / "models"

# Every model the picker offers. The two upstream models are downloaded from
# Hugging Face on first use; the quantized variants are made on this Mac from
# those weights (SECURITY.md's supply-chain stance: weights come from the
# upstream repository, nothing else is fetched) and cost a few minutes once.
# memory_gb is the unified memory the loaded weights take, from the README
# for the upstream models and scaled by the bits for the variants; the
# catalog reports the measured size of a converted variant next to it.
# note is the two-word summary the picker shows; the longer quality
# expectations live in docs/models.md.
MODEL_CATALOG = {
    "1.7b": {
        "label": "1.7B", "note": "accuracy", "repo": "Qwen/Qwen3-ASR-1.7B",
        "memory_gb": 3.4,
    },
    "1.7b-8bit": {
        "label": "1.7B 8-bit", "note": "fastest accurate", "base": "1.7b",
        "bits": 8, "group_size": 64, "memory_gb": 1.9,
    },
    "0.6b": {
        "label": "0.6B", "note": "speed", "repo": "Qwen/Qwen3-ASR-0.6B",
        "memory_gb": 1.2,
    },
    "0.6b-4bit": {
        "label": "0.6B 4-bit", "note": "fastest", "base": "0.6b",
        "bits": 4, "group_size": 64, "memory_gb": 0.5,
    },
}
DEFAULT_MODEL = "1.7b"


def model_source(model_id: str) -> str:
    """What mlx_qwen3_asr loads for a catalog id: a Hub repository for the
    upstream models, the converted directory for a quantized variant."""
    entry = MODEL_CATALOG[model_id]
    if "repo" in entry:
        return entry["repo"]
    return str(MODEL_DIR / f"qwen3-asr-{model_id}")


# Catalog id -> what is loaded, for the places that only need the mapping.
# Read through the module so a test that points MODEL_DIR elsewhere is seen.
MODELS = {model_id: model_source(model_id) for model_id in MODEL_CATALOG}

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
# key-code/modifier-mask table in native/DictationHelper.m. The right-side
# modifiers have unambiguous device-dependent bits and macOS never
# synthesizes them around other keys. Fn is different: macOS synthesizes it
# around every arrow and navigation key, so the helper watches the physical
# key through the keyboard's HID reports instead, and only offers it in the
# menu bar when an attached keyboard has one.
DICTATION_HOTKEYS = {
    "right_command": "Right ⌘",
    "right_option": "Right ⌥",
    "right_control": "Right ⌃",
    "fn": "Fn",
}

# How the push-to-talk key is used. Hold records while the key is down.
# Toggle starts on a tap and stops on the next; a press held longer than a
# tap still behaves as hold, so the two share muscle memory. The ids must
# match native/DictationHelper.m.
DICTATION_MODES = {
    "hold": "Hold to talk",
    "toggle": "Press to start, press to stop",
}
# The recording watchdog: a key-up that never arrives, or a toggle nobody
# ends, must not leave the microphone open forever.
DICTATION_MIN_SECONDS = 60
DICTATION_MAX_SECONDS = 600
DICTATION_DEFAULT_SECONDS = 120

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

# The vocabulary hint is prepended to every chunk's prompt, so it is bounded.
MAX_CONTEXT_CHARS = 2000

# Finished jobs are kept only long enough for the browser to collect the
# result; the transcript itself lives in TRANSCRIPTS_DIR.
JOB_RETENTION_SECONDS = 60 * 60
MAX_REMEMBERED_JOBS = 50
# A job in one of these is settled: safe to evict, no longer cancellable, and
# never resurrected by a worker returning from an in-flight model call.
TERMINAL_JOB_STATUSES = {"done", "error", "cancelled"}


def sweep_stale(paths, max_age_seconds: float = 24 * 60 * 60) -> None:
    """Remove old leftovers from a hard crash.

    Only old files go, so a second accidental server process cannot interfere
    with an active upload of the first.
    """
    for stale in paths:
        try:
            if stale.is_file() and time.time() - stale.stat().st_mtime > max_age_seconds:
                stale.unlink()
        except OSError:
            pass


sweep_stale(UPLOAD_DIR.iterdir())
sweep_stale(TRANSCRIPTS_DIR.glob("*.json.tmp"))
