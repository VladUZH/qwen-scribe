"""
Convert Qwen3-ASR-1.7B to 8-bit quantized weights (one-time, ~2 min).

Produces ./models/qwen3-asr-1.7b-8bit/ which the server picks up
automatically on next start. Expected: ~3x faster decoding, WER delta
within noise of fp16 (per the library's committed benchmarks).

Usage:
    source .venv/bin/activate
    python quantize_8bit.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
from mlx.utils import tree_flatten

from mlx_qwen3_asr.load_models import _resolve_path, load_model

SOURCE = "Qwen/Qwen3-ASR-1.7B"
BITS = 8
GROUP_SIZE = 64
OUT_DIR = Path(__file__).resolve().parent / "models" / "qwen3-asr-1.7b-8bit"


def main() -> None:
    print(f"Loading {SOURCE} (downloads weights on first run)...")
    model, _config = load_model(SOURCE, dtype=mx.float16)
    source_dir = _resolve_path(SOURCE)

    print(f"Quantizing to {BITS}-bit (group size {GROUP_SIZE})...")
    nn.quantize(model, bits=BITS, group_size=GROUP_SIZE)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Saving quantized weights...")
    weights = dict(tree_flatten(model.parameters()))
    mx.save_safetensors(str(OUT_DIR / "model.safetensors"), weights)

    # Copy config + tokenizer files from the source snapshot; skip the
    # original fp16/bf16 weights and index files.
    skipped = {".safetensors", ".bin", ".pt", ".gguf"}
    for f in source_dir.iterdir():
        if f.is_file() and f.suffix not in skipped and "index" not in f.name:
            shutil.copy2(f, OUT_DIR / f.name)

    (OUT_DIR / "quantization_config.json").write_text(
        json.dumps({"bits": BITS, "group_size": GROUP_SIZE}, indent=2)
    )

    size_gb = sum(p.stat().st_size for p in OUT_DIR.iterdir()) / 1e9
    print(f"\nDone: {OUT_DIR}  ({size_gb:.2f} GB)")
    print("Restart the server (./run.sh) — it will use the 8-bit model automatically.")
    print("Recommended: verify on your own audio first:  python compare_models.py <file>")


if __name__ == "__main__":
    main()
