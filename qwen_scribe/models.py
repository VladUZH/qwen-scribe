"""The model catalog: what the picker offers, in what state, and the
on-device conversion that makes a quantized variant from upstream weights.

The two upstream models are downloaded from Hugging Face on first use, as
they always were. A quantized variant is produced on this Mac from those
weights, so nothing is fetched that the upstream model did not already
need, and stored under config.MODEL_DIR. This module never touches the
network itself: the base download goes through sessions.ensure_downloaded,
which reports progress on the job, and the cache checks read the local
Hub cache only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import config, sessions

# What quantize_8bit.py used to write next to server.py; adopted on start.
LEGACY_VARIANT = "1.7b-8bit"

STATES = ("ready", "downloadable", "needs_conversion", "converting")


class ConversionCancelled(Exception):
    """The user cancelled the prepare job; nothing is left behind."""


def ids() -> list[str]:
    return list(config.MODEL_CATALOG)


def is_quantized(model_id: str) -> bool:
    return "bits" in config.MODEL_CATALOG[model_id]


def base_of(model_id: str) -> str:
    """The upstream model a variant is made from; an upstream model itself."""
    return config.MODEL_CATALOG[model_id].get("base", model_id)


def variant_dir(model_id: str) -> Path:
    return Path(config.model_source(model_id))


def source(model_id: str) -> str:
    return config.model_source(model_id)


def label(model_id: str) -> str:
    return config.MODEL_CATALOG[model_id]["label"]


def converted(model_id: str) -> bool:
    """Whether a quantized variant exists on disk, complete."""
    directory = variant_dir(model_id)
    return (directory / "quantization_config.json").is_file() and (directory / "model.safetensors").is_file()


def usable(model_id: str) -> bool:
    """Whether a job may ask for this model now: an upstream model always
    (it downloads on first use), a variant only once converted."""
    return not is_quantized(model_id) or converted(model_id)


def cached(model_id: str) -> bool:
    """Whether an upstream model's weights are in the local Hub cache.

    Read from the cache directory only, never the network: this is asked on
    every page load. A file the Hub library has linked into a snapshot is a
    complete download; a partial one stays a .incomplete blob and is never
    linked, so the presence of any weights file is enough.
    """
    try:
        from huggingface_hub import try_to_load_from_cache

        found = try_to_load_from_cache(config.MODEL_CATALOG[model_id]["repo"], "config.json")
        if not isinstance(found, str):
            return False
        return any(Path(found).parent.glob("*.safetensors"))
    except Exception:
        return False


def _disk_gb(directory: Path) -> float | None:
    try:
        return round(sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) / 1e9, 2)
    except OSError:
        return None


def state(model_id: str, converting: set[str] | frozenset[str] = frozenset()) -> str:
    if is_quantized(model_id):
        if converted(model_id):
            return "ready"
        return "converting" if model_id in converting else "needs_conversion"
    return "ready" if cached(model_id) else "downloadable"


def describe(model_id: str, converting: set[str] | frozenset[str] = frozenset(),
             loaded: set[str] | frozenset[str] = frozenset()) -> dict:
    entry = config.MODEL_CATALOG[model_id]
    quantized = is_quantized(model_id)
    directory = variant_dir(model_id) if quantized else None
    return {
        "id": model_id,
        "label": entry["label"],
        "note": entry["note"],
        "base": entry.get("base"),
        "bits": entry.get("bits"),
        "group_size": entry.get("group_size"),
        "memory_gb": entry["memory_gb"],
        # Measured, once it exists; the weights on disk are what gets loaded.
        "disk_gb": _disk_gb(directory) if directory is not None and converted(model_id) else None,
        "state": state(model_id, converting),
        "loaded": model_id in loaded,
    }


def listing(converting: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    loaded = frozenset(sessions.loaded_models())
    return [describe(model_id, converting, loaded) for model_id in config.MODEL_CATALOG]


def adopt_legacy() -> Path | None:
    """Move a conversion quantize_8bit.py made next to server.py into the
    store, once, so it is not made again. Returns the new path when moved."""
    legacy = config.LEGACY_MODEL_DIR / f"qwen3-asr-{LEGACY_VARIANT}"
    target = variant_dir(LEGACY_VARIANT)
    # Nothing to adopt, or the store already has one (which is also the case
    # when QWEN_SCRIBE_MODEL_DIR points at the legacy directory itself).
    if not (legacy / "quantization_config.json").is_file() or target.exists():
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(target))
    except OSError:
        return None
    return target


def sweep_partials() -> list[Path]:
    """Remove conversions a hard stop left half-written; returns what went.

    A conversion that was still saving when the process died leaves its
    .partial directory, gigabytes that nothing would ever finish or use.
    """
    removed = []
    try:
        candidates = list(config.MODEL_DIR.glob("qwen3-asr-*.partial"))
    except OSError:
        return removed
    for candidate in candidates:
        try:
            shutil.rmtree(candidate)
            removed.append(candidate)
        except OSError:
            pass
    return removed


def remove(model_id: str) -> bool:
    """Delete a converted variant from disk; True when something was removed."""
    if not is_quantized(model_id):
        raise ValueError(f"{label(model_id)} is downloaded by the Hub library; there is nothing to remove here")
    directory = variant_dir(model_id)
    sessions.drop(source(model_id))
    removed = False
    for candidate in (directory, directory.with_name(directory.name + ".partial")):
        if candidate.exists():
            shutil.rmtree(candidate)
            removed = True
    return removed


# Files copied from the upstream snapshot next to the quantized weights: the
# configuration and tokenizer. The upstream weights themselves stay in the
# Hub cache, where the fp16 model still loads from.
_WEIGHT_SUFFIXES = {".safetensors", ".bin", ".pt", ".gguf"}


def convert(model_id: str, *, report=None, cancelled=None) -> Path:
    """Make a quantized variant from its upstream model's weights.

    ``report(detail)`` receives a short status line at each step. ``cancelled``
    is a threading.Event; a cancel is honoured between steps and after the
    save, in which case nothing remains on disk. The upstream weights must
    already be in the Hub cache (sessions.ensure_downloaded does that with a
    progress report). Loads the fp16 model into memory for the duration, so
    the caller releases loaded sessions first.
    """
    if not is_quantized(model_id):
        raise ValueError(f"{label(model_id)} is an upstream model and is not converted")
    entry = config.MODEL_CATALOG[model_id]
    repo = config.MODEL_CATALOG[entry["base"]]["repo"]
    target = variant_dir(model_id)
    partial = target.with_name(target.name + ".partial")

    def say(detail: str) -> None:
        if report is not None:
            report(detail)

    def check() -> None:
        if cancelled is not None and cancelled.is_set():
            raise ConversionCancelled

    from huggingface_hub import snapshot_download

    source_dir = Path(snapshot_download(repo_id=repo, allow_patterns=sessions.WEIGHT_PATTERNS))
    check()

    import mlx.core as mx
    import mlx.nn as nn
    from mlx.utils import tree_flatten
    from mlx_qwen3_asr.load_models import load_model

    say(f"Loading {label(entry['base'])}")
    model, _config = load_model(repo, dtype=mx.float16)
    try:
        check()
        say(f"Quantizing to {entry['bits']}-bit")
        nn.quantize(model, bits=entry["bits"], group_size=entry["group_size"])
        weights = dict(tree_flatten(model.parameters()))
        check()
        say("Saving")
        if partial.exists():
            shutil.rmtree(partial)
        partial.mkdir(parents=True)
        mx.save_safetensors(str(partial / "model.safetensors"), weights)
        for item in source_dir.iterdir():
            if item.is_file() and item.suffix not in _WEIGHT_SUFFIXES and "index" not in item.name:
                shutil.copy2(item, partial / item.name)
        (partial / "quantization_config.json").write_text(
            json.dumps({"bits": entry["bits"], "group_size": entry["group_size"]}, indent=2)
        )
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    finally:
        del model
        sessions._release_memory()
    if cancelled is not None and cancelled.is_set():
        shutil.rmtree(partial, ignore_errors=True)
        raise ConversionCancelled
    if target.exists():
        shutil.rmtree(target)
    partial.rename(target)
    return target
