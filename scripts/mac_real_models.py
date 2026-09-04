"""The model catalogue for real, on a Mac: convert, transcribe, compare, remove.

Run after the app's launcher has started the server. This is the one pass
that exercises the parts nothing else can: MLX quantizing the real weights,
a converted model loading and transcribing, and the numbers that go into
docs/models.md. It uses the same synthesised speech as mac_checks.py, and
the same HTTP API the page and the helper use, so the code under test is
exactly what a person's Mac runs.

    python scripts/mac_real_models.py --out artifacts-models [--variant 0.6b-4bit]

Exits non-zero when a check fails. Standard library only, plus this
repository's own modules.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mac_checks as mc                      # noqa: E402  (same directory)
from compare_models import word_diff_rate    # noqa: E402  (repository root)

record, request = mc.record, mc.request


def catalog() -> dict[str, dict]:
    _status, body = request("GET", "/api/models")
    return {item["id"]: item for item in json.loads(body)["models"]}


def wait_for_state(model_id: str, state: str, timeout: float = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if catalog()[model_id]["state"] == state:
            return True
        time.sleep(0.5)
    return False


def transcribe(transcriber, label: str, wav: Path, model: str, out: Path) -> tuple[str, float]:
    """Transcribe one sample with one model; returns (text, seconds decoding)."""
    job, _seen = transcriber.run(label, wav, wav.name,
                                {"model": model, "language": "auto", "timestamps": "false"})
    (out / "transcripts" / f"{label}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2))
    if job["status"] != "done":
        record("FAIL", f"{label}: transcribed with {model}", (job.get("detail") or "")[:120])
        return "", 0.0
    text = (job["result"].get("text") or "").strip()
    elapsed = (job.get("finished_at") or 0) - (job.get("started_at") or 0)
    record("PASS" if text else "FAIL", f"{label}: transcribed with {model}",
           f"{elapsed:.1f}s · {text[:60]}")
    return text, elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--variant", default="0.6b-4bit", help="the variant to convert and measure")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds allowed per job")
    parser.add_argument("--restart-command", default=None,
                        help="shell command that stops and relaunches the server; used after a GPU timeout")
    args = parser.parse_args()
    out: Path = args.out
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    variant = args.variant

    if not mc.wait_for_server():
        record("FAIL", "server answers on 127.0.0.1:8990")
        return mc.finish(out)
    record("PASS", "server answers on 127.0.0.1:8990")

    entries = catalog()
    base = entries[variant]["base"]
    # Removing the variant at the end must not have cost the upstream model
    # its weights, so remember where it started.
    base_state_before = entries[base]["state"]
    record("PASS" if len(entries) == 4 and base else "FAIL",
           "the catalogue offers the two models and a variant of each",
           ", ".join(f"{key}:{item['state']}" for key, item in entries.items()))

    # Start from unprepared, so the conversion below is the real first one.
    if entries[variant]["state"] == "ready":
        status, _ = request("DELETE", f"/api/models/{variant}")
        record("PASS" if status == 204 else "FAIL", f"{variant}: an earlier conversion removed first")

    # A file sent for a variant that is not prepared is refused, not queued.
    voices = mc.installed_voices()
    english = mc.pick_voice(voices, mc.SAMPLES[0][2], "en")
    if english is None:
        record("FAIL", "an English voice is installed on this runner")
        return mc.finish(out)
    probe = out / "audio" / "probe.wav"
    mc.synthesise(english, "Ready.", probe)
    transcriber = mc.Transcriber(args.restart_command, args.timeout)

    # The first thing a fresh process asks of the runner's virtual GPU is
    # where its watchdog trips, and the conversion below is heavy GPU work.
    # A two-word clip takes that hit instead, and fetches the upstream
    # weights the conversion needs anyway.
    warm, warm_seen = transcriber.run("warm-up", probe, "warm-up.wav",
                                      {"model": base, "language": "English", "timestamps": "false"})
    downloads = [d for _t, _s, d in warm_seen if d.startswith("Downloading model")]
    record("PASS", f"{base}: weights {'downloaded' if downloads else 'already cached'}",
           downloads[-1] if downloads else "")
    if warm["status"] == "done":
        record("PASS", "warm-up decode done, GPU kernels ready")
    elif mc.gpu_hiccup(warm):
        record("WARN", "the runner's GPU watchdog tripped on the first decode; the pass continues",
               (warm.get("detail") or "")[:110])
    else:
        record("FAIL", "warm-up decode done", (warm.get("detail") or "")[:110])
        return mc.finish(out)

    try:
        mc.upload(probe, "probe.wav", {"model": variant, "language": "auto", "timestamps": "false"})
        record("FAIL", f"{variant}: an upload is refused until it is prepared", "the server accepted it")
    except RuntimeError as exc:
        record("PASS" if "not prepared" in str(exc) else "FAIL",
               f"{variant}: an upload is refused until it is prepared", str(exc)[:90])

    # ── The conversion itself: MLX quantizing the real weights ───────────
    status, body = request("POST", f"/api/models/{variant}/prepare")
    if status != 200:
        record("FAIL", f"{variant}: the conversion is accepted", body.decode(errors="replace")[:120])
        return mc.finish(out)
    job_id = json.loads(body)["id"]
    record("PASS", f"{variant}: the conversion is queued as a job")

    second, _ = request("POST", f"/api/models/{variant}/prepare")
    record("PASS" if second == 409 else "FAIL",
           f"{variant}: a second conversion is refused while the first runs", f"HTTP {second}")

    started = time.time()
    try:
        job, seen = mc.wait_for_job(job_id, args.timeout)
    except TimeoutError as exc:
        record("FAIL", f"{variant}: the conversion finishes", str(exc))
        return mc.finish(out)
    convert_seconds = time.time() - started
    steps = [detail for _t, _s, detail in seen if detail]
    if job["status"] != "done":
        record("FAIL", f"{variant}: the conversion finishes", (job.get("detail") or "")[:160])
        return mc.finish(out)
    record("PASS", f"{variant}: converted in {convert_seconds / 60:.1f} min", " → ".join(steps[:6]))

    prepared = catalog()[variant]
    record("PASS" if prepared["state"] == "ready" else "FAIL", f"{variant}: the catalogue reports it ready")
    on_disk = prepared.get("disk_gb")
    record("PASS" if on_disk and on_disk > 0 else "FAIL",
           f"{variant}: the weights are on disk", f"{on_disk} GB")
    # The picker promises a memory figure; weights that are wildly smaller or
    # larger on disk mean that promise, or the conversion, is wrong.
    if on_disk:
        expected = prepared["memory_gb"]
        record("PASS" if 0.4 <= on_disk / expected <= 2.0 else "WARN",
               f"{variant}: its size matches what the picker promises",
               f"{on_disk} GB on disk against {expected} GB in memory")
        # What proves the weights were quantized rather than copied. The job's
        # own step lines are the wrong thing to assert on: quantizing a 0.6B
        # model takes under a second, so polling can miss the line entirely,
        # and tests/test_prepare.py already pins the exact sequence.
        fp16_size = entries[base]["memory_gb"]
        record("PASS" if on_disk < fp16_size * 0.75 else "FAIL",
               f"{variant}: the weights on disk are quantized, not a copy of fp16",
               f"{on_disk} GB against {fp16_size} GB of fp16 weights")

    # ── The same speech through both models ──────────────────────────────
    rows: list[str] = []
    for name, locale, preferred, text, expected in mc.SAMPLES:
        voice = mc.pick_voice(voices, preferred, locale)
        if voice is None:
            record("SKIP", f"{name}: no {locale} voice on this runner")
            continue
        wav = out / "audio" / f"{name}.wav"
        seconds = mc.synthesise(voice, text, wav)
        reference, fp16_seconds = transcribe(transcriber, f"{name}-{base}", wav, base, out)
        quantized, variant_seconds = transcribe(transcriber, f"{name}-{variant}", wav, variant, out)
        if not reference or not quantized:
            continue
        rate, spans = word_diff_rate(reference, quantized)
        speedup = fp16_seconds / variant_seconds if variant_seconds else 0
        # Quantization is lossy by design, so a difference is not a failure;
        # a transcript with nothing in common with fp16 means the conversion
        # itself is broken, and that is.
        record("FAIL" if rate > 0.6 else "WARN" if rate > 0.25 else "PASS",
               f"{name}: the variant stays close to fp16",
               f"{rate:.1%} of comparable units differ")
        rows.append(f"| {name} ({expected}) | {seconds:.1f}s | {fp16_seconds:.1f}s | "
                    f"{variant_seconds:.1f}s | {speedup:.2f}x | {rate:.1%} |")
        (out / "transcripts" / f"{name}-diff.txt").write_text(
            f"fp16 ({base}):\n{reference}\n\n{variant}:\n{quantized}\n\n"
            + "\n".join(spans) + "\n")

    # As the helper sends a dictation, with the variant chosen for dictation.
    english_wav = out / "audio" / "english.wav"
    if english_wav.exists():
        job, _ = transcriber.run("dictation", english_wav, "Dictation 2026-09-04 21.00.00.wav",
                                 {"model": variant, "language": "auto", "timestamps": "false",
                                  "context": "Qwen Scribe", "source": "dictation"})
        ok = job["status"] == "done" and bool((job["result"].get("text") or "").strip())
        record("PASS" if ok else "FAIL", f"{variant}: a dictation transcribes with the variant",
               (job["result"].get("text") if ok else job.get("detail", ""))[:70])

    # ── Removing it puts the Mac back where it started ───────────────────
    status, _ = request("DELETE", f"/api/models/{variant}")
    record("PASS" if status == 204 else "FAIL", f"{variant}: Remove answers 204", f"HTTP {status}")
    record("PASS" if wait_for_state(variant, "needs_conversion") else "FAIL",
           f"{variant}: the catalogue reports it unprepared again")
    base_state_after = catalog()[base]["state"]
    record("PASS" if base_state_after in ("ready", base_state_before) else "FAIL",
           f"{base}: the upstream model it was made from is untouched",
           f"{base_state_before} → {base_state_after}")

    if rows:
        table = ("| Sample | Audio | fp16 | " + variant + " | Speed-up | Words differing |\n"
                 "| --- | --- | --- | --- | --- | --- |\n" + "\n".join(rows))
        (out / "timings.md").write_text(
            f"Converted {variant} in {convert_seconds / 60:.1f} minutes, {on_disk} GB on disk.\n\n"
            + table + "\n")
    return mc.finish(out)


if __name__ == "__main__":
    sys.exit(main())
