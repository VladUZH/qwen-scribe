"""
Prepare a quantized model from the command line (the app's model picker does
the same with a Prepare button). Loads the upstream fp16 weights, quantizes
them on this Mac, and writes the variant into the app's model store, where
the server offers it in the picker on its next start.

Usage:
    source .venv/bin/activate
    python quantize_8bit.py              # 1.7B 8-bit, the original purpose of this script
    python quantize_8bit.py 0.6b-4bit    # any quantized variant in the catalog

Expected for 1.7B 8-bit: ~3x faster decoding, WER delta within noise of fp16
(per the library's committed benchmarks); docs/models.md has the details and
compare_models.py measures it on your own recordings.
"""

from __future__ import annotations

import sys

from qwen_scribe import models


def main() -> None:
    choices = [m for m in models.ids() if models.is_quantized(m)]
    variant = sys.argv[1] if len(sys.argv) > 1 else "1.7b-8bit"
    if variant not in choices:
        sys.exit(f"Unknown variant '{variant}'. Choose one of: {', '.join(choices)}")
    if models.converted(variant):
        sys.exit(f"{models.label(variant)} is already prepared at {models.source(variant)}")
    base = models.base_of(variant)
    print(f"Preparing {models.label(variant)} from {models.label(base)} (downloads its weights on first run)...")
    target = models.convert(variant, report=lambda detail: print(f"  {detail}..."))
    entry = models.describe(variant)
    print(f"\nDone: {target}  ({entry['disk_gb']} GB)")
    print("Restart the server (./run.sh or the app) and choose it in the model picker.")
    print(f"Recommended: verify on your own audio first:  python compare_models.py --variant {variant} <file>")


if __name__ == "__main__":
    main()
