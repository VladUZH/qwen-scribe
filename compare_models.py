"""
A/B check: the fp16 upstream model against a quantized variant the app has
prepared, on YOUR audio (the noisy recordings you actually care about).

Usage:
    source .venv/bin/activate
    python compare_models.py meeting.m4a [more files...]
    python compare_models.py --variant 0.6b-4bit --language English meeting.m4a

Without --variant every prepared variant in the catalog is compared with its
upstream model (1.7b-8bit against 1.7b, 0.6b-4bit against 0.6b). Reports per
file: both runtimes, and the word-level difference rate of the quantized
transcript measured against the fp16 transcript (fp16 treated as reference).
Under ~1% difference with no changed names/numbers = the quantization is
lossless for your purposes. docs/models.md explains what to expect.

Variants are prepared from the app's model picker, or with quantize_8bit.py.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import time

from qwen_scribe import models


# Chinese, Japanese and Korean: Han, Hiragana, Katakana and Hangul. Japanese
# and Chinese are written without spaces at all, and Korean spacing is a
# choice the model makes ("세 시에" or "세시에" for the same words), so a
# whitespace-delimited word is not a unit of meaning in any of them.
CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff"
_TOKEN = re.compile(f"[{CJK}]|[^\\W{CJK}]+(?:'[^\\W{CJK}]+)*")


def words(text: str) -> list[str]:
    """Comparable units: Latin words, and CJK characters one at a time.

    Splitting CJK on whitespace would report two transcripts of the same
    sentence as wholly different, and hide a single wrong character in a
    long run. Punctuation is left out either way: it is not what a
    quantized model is being judged on.
    """
    return _TOKEN.findall(text.lower())


def word_diff_rate(reference: str, hypothesis: str) -> tuple[float, list[str]]:
    """Word-level edit distance rate + human-readable diff snippets."""
    ref, hyp = words(reference), words(hypothesis)
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    edits, snippets = 0, []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        edits += max(i2 - i1, j2 - j1)
        snippets.append(f"  fp16: {' '.join(ref[i1:i2]) or '∅'}  →  quantized: {' '.join(hyp[j1:j2]) or '∅'}")
    return (edits / max(len(ref), 1), snippets)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="audio or video files to transcribe with both models")
    parser.add_argument("--variant", action="append", choices=[m for m in models.ids() if models.is_quantized(m)],
                        help="a prepared variant to compare (default: every prepared one)")
    parser.add_argument("--language", default="auto", help="language to pass the model (default: auto-detect)")
    args = parser.parse_args()

    variants = args.variant or [m for m in models.ids() if models.is_quantized(m) and models.converted(m)]
    missing = [m for m in variants if not models.converted(m)]
    if missing:
        sys.exit(f"Not prepared: {', '.join(missing)}. Prepare it from the app's model picker "
                 f"or run: python quantize_8bit.py {missing[0]}")
    if not variants:
        sys.exit("No prepared variant found. Prepare one from the app's model picker first.")

    from mlx_qwen3_asr import Session

    kwargs = {} if args.language == "auto" else {"language": args.language}
    for variant in variants:
        base = models.base_of(variant)
        print(f"\n##### {models.label(variant)} against {models.label(base)} (fp16)")
        print(f"Loading {models.label(base)}...")
        fp16 = Session(model=models.source(base))
        print(f"Loading {models.label(variant)} from {models.source(variant)}...")
        quantized = Session(model=models.source(variant))

        for file in args.files:
            print(f"\n=== {file} ===")
            t0 = time.time()
            r_fp16 = fp16.transcribe(file, **kwargs)
            t_fp16 = time.time() - t0

            t0 = time.time()
            r_q = quantized.transcribe(file, **kwargs)
            t_q = time.time() - t0

            rate, snippets = word_diff_rate(r_fp16.text, r_q.text)
            print(f"fp16: {t_fp16:6.1f}s   quantized: {t_q:6.1f}s   speedup: {t_fp16 / max(t_q, 1e-9):.2f}x")
            print(f"word difference rate (vs fp16): {rate:.2%}  ({len(snippets)} diff spans)")
            for snippet in snippets[:15]:
                print(snippet)
            if len(snippets) > 15:
                print(f"  ... and {len(snippets) - 15} more")
        del fp16, quantized


if __name__ == "__main__":
    main()
