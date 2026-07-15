"""
A/B check: fp16 Qwen3-ASR-1.7B vs your local 8-bit conversion,
on YOUR audio (the noisy recordings you actually care about).

Usage:
    source .venv/bin/activate
    python compare_models.py meeting.m4a [more files...]

Reports per file: both runtimes, and the word-level difference rate of the
8-bit transcript measured against the fp16 transcript (fp16 treated as
reference). Under ~1% difference with no changed names/numbers = the
quantization is lossless for your purposes.
"""

from __future__ import annotations

import difflib
import re
import sys
import time
from pathlib import Path

from mlx_qwen3_asr import Session

FP16 = "Qwen/Qwen3-ASR-1.7B"
QUANT = Path(__file__).resolve().parent / "models" / "qwen3-asr-1.7b-8bit"


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def word_diff_rate(reference: str, hypothesis: str) -> tuple[float, list[str]]:
    """Word-level edit distance rate + human-readable diff snippets."""
    ref, hyp = words(reference), words(hypothesis)
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    edits, snippets = 0, []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        edits += max(i2 - i1, j2 - j1)
        snippets.append(f"  fp16: {' '.join(ref[i1:i2]) or '∅'}  →  8bit: {' '.join(hyp[j1:j2]) or '∅'}")
    return (edits / max(len(ref), 1), snippets)


def main() -> None:
    files = sys.argv[1:]
    if not files:
        sys.exit("Usage: python compare_models.py <audio-or-video-file> [...]")
    if not (QUANT / "quantization_config.json").exists():
        sys.exit("8-bit model not found — run: python quantize_8bit.py")

    print("Loading fp16 model...")
    fp16 = Session(model=FP16)
    print("Loading 8-bit model...")
    q8 = Session(model=str(QUANT))

    for f in files:
        print(f"\n=== {f} ===")
        t0 = time.time()
        r_fp16 = fp16.transcribe(f, language="English")
        t_fp16 = time.time() - t0

        t0 = time.time()
        r_q8 = q8.transcribe(f, language="English")
        t_q8 = time.time() - t0

        rate, snippets = word_diff_rate(r_fp16.text, r_q8.text)
        print(f"fp16: {t_fp16:6.1f}s   8-bit: {t_q8:6.1f}s   speedup: {t_fp16 / max(t_q8, 1e-9):.2f}x")
        print(f"word difference rate (vs fp16): {rate:.2%}  ({len(snippets)} diff spans)")
        for s in snippets[:15]:
            print(s)
        if len(snippets) > 15:
            print(f"  ... and {len(snippets) - 15} more")


if __name__ == "__main__":
    main()
