"""
A/B check: the fp16 upstream model against a quantized variant the app has
prepared, on YOUR audio (the noisy recordings you actually care about).

Usage:
    source .venv/bin/activate
    python compare_models.py meeting.m4a [more files...]
    python compare_models.py --variant 0.6b-4bit --language English meeting.m4a
    python compare_models.py --model fourier-2b-8bit --reference 1.7b --language English meeting.m4a

Without --variant or --model every prepared variant in the catalog is compared
with its upstream model (1.7b-8bit against 1.7b, 0.6b-4bit against 0.6b). Use
--model with --reference to compare any two catalog entries (for example,
fourier-2b-8bit against 1.7b). Reports per file: both runtimes, and the
word-level difference rate of the candidate transcript measured against the
reference transcript. Under ~1% difference with no changed names/numbers = the
quantization is lossless for your purposes. docs/models.md explains what to
expect.

Variants are prepared from the app's model picker, or with quantize_8bit.py.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import time

from qwen_scribe import models, sessions


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


def comparison_pairs(
    variants: list[str] | None = None,
    candidates: list[str] | None = None,
    reference: str = "1.7b",
) -> list[tuple[str, str]]:
    """Return ``(reference_id, candidate_id)`` pairs to benchmark."""
    if candidates:
        return [(reference, candidate) for candidate in candidates]
    chosen = variants or [m for m in models.ids() if models.is_quantized(m) and models.converted(m)]
    return [(models.base_of(variant), variant) for variant in chosen]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="audio or video files to transcribe with both models")
    parser.add_argument("--variant", action="append", choices=[m for m in models.ids() if models.is_quantized(m)],
                        help="a prepared variant to compare against its base model (default: every prepared one)")
    parser.add_argument("--model", action="append", choices=models.ids(),
                        help="a catalog model to compare against --reference (e.g. fourier-2b-8bit)")
    parser.add_argument("--reference", default="1.7b", choices=models.ids(),
                        help="reference model for --model comparisons (default: 1.7b)")
    parser.add_argument("--language", default="auto", help="language to pass the model (default: auto-detect)")
    args = parser.parse_args()

    if args.variant and args.model:
        sys.exit("Pass either --variant or --model, not both.")

    pairs = comparison_pairs(variants=args.variant, candidates=args.model, reference=args.reference)
    missing = [m for pair in pairs for m in pair if not models.usable(m)]
    if missing:
        unique_missing = list(dict.fromkeys(missing))
        sys.exit(f"Not prepared: {', '.join(unique_missing)}. Prepare it from the app's model picker "
                 f"or run: python quantize_8bit.py {unique_missing[0]}")
    if not pairs:
        sys.exit("No prepared variant found. Prepare one from the app's model picker first.")

    from mlx_qwen3_asr import Session

    kwargs = {} if args.language == "auto" else {"language": args.language}
    for base, candidate in pairs:
        print(f"\n##### {models.label(candidate)} against {models.label(base)}")
        print(f"Loading {models.label(base)}...")
        ref_session = Session(model=sessions.resolve_model_target(base))
        print(f"Loading {models.label(candidate)}...")
        cand_session = Session(model=sessions.resolve_model_target(candidate))

        for file in args.files:
            print(f"\n=== {file} ===")
            t0 = time.time()
            r_ref = ref_session.transcribe(file, **kwargs)
            t_ref = time.time() - t0

            t0 = time.time()
            r_cand = cand_session.transcribe(file, **kwargs)
            t_cand = time.time() - t0

            rate, snippets = word_diff_rate(r_ref.text, r_cand.text)
            print(f"reference: {t_ref:6.1f}s   candidate: {t_cand:6.1f}s   speedup: {t_ref / max(t_cand, 1e-9):.2f}x")
            print(f"word difference rate (vs {models.label(base)}): {rate:.2%}  ({len(snippets)} diff spans)")
            for snippet in snippets[:15]:
                print(snippet)
            if len(snippets) > 15:
                print(f"  ... and {len(snippets) - 15} more")
        del ref_session, cand_session


if __name__ == "__main__":
    main()
