"""Field test on a Mac: speech synthesised by macOS through the real server.

Run after the app's launcher has started the server on 127.0.0.1:8990. Uses
macOS's own `say` and `afconvert` to make short English, Korean, and Japanese
recordings, transcribes them through the real model with word timestamps,
sends one as the native helper would (a dictation with a dictionary hint),
and writes the transcripts, the audio, and a Markdown summary to --out. Exits
non-zero when a check fails. Standard library only.

The runner's virtual GPU is slow, and the first decode in a server process
is the heavy one: on a bad host it trips the Metal command-buffer watchdog.
Later decodes in the same process are fine whether or not that first one
was, so the checks warm the GPU with a two-word clip first and tolerate a
timeout there. A timeout on a real sample after that is retried once, after
stopping and relaunching the server the way the apps do (--restart-command),
which is what a person would do at the Mac; a second failure is reported as
one.

This is what the maintainer would do by hand before a release, minus the
parts that need a person at the keyboard: granting Microphone, Input
Monitoring and Accessibility, holding the push-to-talk key, and watching the
paste land.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

BASE = "http://127.0.0.1:8990"

# (name, locale prefix, preferred voices, text, language the model should say)
SAMPLES = [
    ("english", "en", ["Samantha", "Alex", "Daniel", "Karen", "Moira", "Tessa"],
     "Qwen Scribe transcribes speech on this Mac. It runs locally, and nothing "
     "leaves the machine. The meeting starts at three.", "English"),
    ("korean", "ko", ["Yuna", "Jian", "Suhyun"],
     "안녕하세요. 오늘 날씨가 정말 좋네요. 회의는 세 시에 시작합니다.", "Korean"),
    ("japanese", "ja", ["Kyoko", "Otoya", "Hattori", "O-ren"],
     "こんにちは。今日はいい天気ですね。会議は三時に始まります。", "Japanese"),
]

results: list[tuple[str, str, str]] = []   # (status, check, detail)


def record(status: str, check: str, detail: str = "") -> None:
    results.append((status, check, detail))
    print(f"{status:<5} {check}" + (f"  [{detail}]" if detail else ""), flush=True)


def request(method: str, path: str, data: bytes | None = None, headers: dict | None = None,
            timeout: float = 60):
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def wait_for_server(timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = request("GET", "/api/config", timeout=5)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def installed_voices() -> dict[str, str]:
    """{voice name: locale} from `say -v ?`."""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
    voices = {}
    for line in out.splitlines():
        # "Samantha            en_US    # Hello! My name is Samantha."
        head = line.split("#", 1)[0].rstrip()
        if not head:
            continue
        parts = head.split()
        if len(parts) >= 2:
            voices[" ".join(parts[:-1])] = parts[-1]
    return voices


def pick_voice(voices: dict[str, str], preferred: list[str], locale_prefix: str) -> str | None:
    for name in preferred:
        if name in voices:
            return name
    for name, locale in voices.items():
        if locale.lower().startswith(locale_prefix):
            return name
    return None


def synthesise(voice: str, text: str, out_wav: Path) -> float:
    """Speak text with `say`, convert to 16 kHz mono 16-bit WAV, return seconds."""
    aiff = out_wav.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                    str(aiff), str(out_wav)], check=True)
    aiff.unlink(missing_ok=True)
    with wave.open(str(out_wav), "rb") as handle:
        assert handle.getframerate() == 16000 and handle.getnchannels() == 1
        return handle.getnframes() / 16000


def upload(path: Path, filename: str, fields: dict[str, str]) -> str:
    boundary = "MacChecksBoundary"
    body = b""
    for key, value in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{filename}\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
    body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    status, out = request("POST", "/api/jobs", body,
                          {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if status != 200:
        raise RuntimeError(f"upload refused ({status}): {out.decode(errors='replace')[:200]}")
    return json.loads(out)["id"]


def wait_for_job(job_id: str, timeout: float) -> tuple[dict, list[tuple[float, str, str]]]:
    """Poll to a terminal state; also return (t, status, detail) transitions."""
    seen: list[tuple[float, str, str]] = []
    started = time.time()
    while time.time() - started < timeout:
        status, out = request("GET", f"/api/jobs/{job_id}")
        job = json.loads(out)
        stamp = (job["status"], job.get("detail") or "")
        if not seen or (seen[-1][1], seen[-1][2]) != stamp:
            seen.append((time.time() - started, *stamp))
        if job["status"] in ("done", "error", "cancelled"):
            return job, seen
        time.sleep(0.5)
    raise TimeoutError(f"job {job_id} still {job['status']} after {timeout:.0f}s: {job.get('detail')}")


GPU_ERROR_MARKERS = ("[METAL]", "kIOGPUCommandBuffer", "GPU Timeout")


def gpu_hiccup(job: dict) -> bool:
    """A Metal command-buffer failure: the runner's virtual GPU, not the app."""
    detail = job.get("detail") or ""
    return job.get("status") == "error" and any(marker in detail for marker in GPU_ERROR_MARKERS)


def restart_server(command: str) -> bool:
    """Stop and relaunch the server as the apps do; True once it answers again."""
    subprocess.run(command, shell=True)
    return wait_for_server()


class Transcriber:
    """Uploads and waits, restarting the server once per run after a GPU hiccup."""

    def __init__(self, restart_command: str | None, timeout: float):
        self.restart_command = restart_command
        self.timeout = timeout
        self.restarted = False

    def run(self, label: str, path: Path, filename: str, fields: dict[str, str]):
        job, seen = wait_for_job(upload(path, filename, fields), self.timeout)
        if gpu_hiccup(job) and self.restart_command and not self.restarted:
            self.restarted = True
            record("WARN", f"{label}: the runner's GPU timed out; restarting the server and retrying once",
                   (job.get("detail") or "")[:120])
            if not restart_server(self.restart_command):
                record("FAIL", f"{label}: server answers again after the restart")
                return job, seen
            job, seen = wait_for_job(upload(path, filename, fields), self.timeout)
        return job, seen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="0.6b", help="model key to transcribe with")
    parser.add_argument("--timeout", type=float, default=900,
                        help="seconds allowed per job, first one includes the weight download")
    parser.add_argument("--restart-command", default=None,
                        help="shell command that stops and relaunches the server; used once, after a GPU timeout")
    args = parser.parse_args()
    transcriber = Transcriber(args.restart_command, args.timeout)
    out: Path = args.out
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)

    if not wait_for_server():
        record("FAIL", "server answers on 127.0.0.1:8990")
        return finish(out)
    record("PASS", "server answers on 127.0.0.1:8990")

    status, body = request("GET", "/api/settings")
    settings = json.loads(body)
    record("PASS" if {"dictation", "transcription", "performance"} <= set(settings) else "FAIL",
           "settings carry the dictation, transcription and performance sections")
    status, body = request("GET", "/api/dictation/status")
    record("PASS" if json.loads(body)["available"] is False else "FAIL",
           "dictation status reports no helper on a headless runner")

    for missing in [tool for tool in ("say", "afconvert") if shutil.which(tool) is None]:
        record("FAIL", f"{missing} is available")
        return finish(out)
    voices = installed_voices()
    record("PASS" if voices else "FAIL", "macOS speech voices are installed", f"{len(voices)} voices")

    # The first decode in a fresh process is where the runner's GPU trips its
    # watchdog; let a two-word clip take that hit, and the download report.
    english_voice = pick_voice(voices, SAMPLES[0][2], "en")
    if english_voice is not None:
        wav = out / "audio" / "warm-up.wav"
        seconds = synthesise(english_voice, "Ready now.", wav)
        try:
            job, seen = wait_for_job(upload(wav, "warm-up.wav", {"model": args.model, "language": "English",
                                                                  "timestamps": "false"}), args.timeout)
        except TimeoutError as exc:
            record("FAIL", "warm-up decode finished", str(exc))
        else:
            downloads = [d for _, _, d in seen if d.startswith("Downloading model")]
            record("PASS", f"warm-up: first run {'reported the download' if downloads else 'used cached weights'}",
                   downloads[-1] if downloads else "")
            elapsed = (job.get("finished_at") or 0) - (job.get("started_at") or 0)
            if job["status"] == "done":
                record("PASS", f"warm-up: {seconds:.1f}s clip decoded, GPU kernels ready", f"{elapsed:.1f}s")
            elif gpu_hiccup(job):
                record("WARN", "warm-up: the runner's GPU watchdog tripped on the first decode; the samples follow",
                       (job.get("detail") or "")[:120])
            else:
                record("FAIL", "warm-up decode finished", job.get("detail", ""))

    timings: list[str] = []
    for name, locale, preferred, text, expected_language in SAMPLES:
        voice = pick_voice(voices, preferred, locale)
        if voice is None:
            record("SKIP", f"{name}: no {locale} voice installed on this runner")
            continue
        wav = out / "audio" / f"{name}.wav"
        seconds = synthesise(voice, text, wav)
        record("PASS", f"{name}: synthesised {seconds:.1f}s with voice {voice}")

        try:
            job, seen = transcriber.run(name, wav, f"{name}.wav",
                                        {"model": args.model, "language": "auto", "timestamps": "true"})
        except TimeoutError as exc:
            record("FAIL", f"{name}: transcription finished", str(exc))
            continue
        (out / "transcripts" / f"{name}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2))
        if job["status"] != "done":
            record("FAIL", f"{name}: transcription finished", job.get("detail", ""))
            continue
        result = job["result"]
        text_out = (result.get("text") or "").strip()
        record("PASS" if text_out else "FAIL", f"{name}: transcript has text", text_out[:80])
        detected = result.get("language")
        record("PASS" if detected == expected_language else "WARN",
               f"{name}: language detected as {expected_language}", f"got {detected}")
        if job.get("timestamps_unavailable"):
            record("FAIL", f"{name}: word timestamps produced", job["timestamps_unavailable"][:120])
        else:
            segments = result.get("segments") or []
            record("PASS" if segments else "FAIL", f"{name}: word timestamps produced", f"{len(segments)} words")
        loading_to_done = next((t for t, s, _ in seen if s == "processing"), None)
        elapsed = (job.get("finished_at") or 0) - (job.get("started_at") or 0)
        timings.append(f"| {name} | {seconds:.1f}s | {loading_to_done or 0:.1f}s | {elapsed:.1f}s | "
                       f"{(seconds / elapsed) if elapsed else 0:.1f}x |")

    # As the native helper sends a dictation: source labelled, the dictionary as the hint.
    english = out / "audio" / "english.wav"
    if english.exists():
        try:
            job, _ = transcriber.run("dictation", english, "Dictation 2026-09-04 09.00.00.wav",
                                     {"model": args.model, "language": "auto", "timestamps": "false",
                                      "turbo": "false", "context": "Qwen Scribe", "source": "dictation"})
            ok = job["status"] == "done" and bool((job["result"].get("text") or "").strip())
            record("PASS" if ok else "FAIL", "dictation-shaped upload with a dictionary hint transcribes",
                   (job["result"].get("text") if ok else job.get("detail", ""))[:80])
            (out / "transcripts" / "dictation.json").write_text(json.dumps(job, ensure_ascii=False, indent=2))
        except TimeoutError as exc:
            record("FAIL", "dictation-shaped upload with a dictionary hint transcribes", str(exc))

    status, body = request("GET", "/api/transcripts")
    saved = json.loads(body)["transcripts"]
    record("PASS" if len(saved) >= 1 else "FAIL", "transcripts were saved to history", f"{len(saved)} saved")

    if timings:
        summary_table = ("| Sample | Audio | Model load + decode wait | Transcribe | Speed |\n"
                         "| --- | --- | --- | --- | --- |\n" + "\n".join(timings))
        (out / "timings.md").write_text(summary_table + "\n")
    return finish(out)


def finish(out: Path) -> int:
    counts = {status: sum(1 for s, _, _ in results if s == status) for status in ("PASS", "WARN", "SKIP", "FAIL")}
    lines = ["## Mac checks", "", f"PASS {counts['PASS']} · WARN {counts['WARN']} · SKIP {counts['SKIP']} · FAIL {counts['FAIL']}", ""]
    lines += [f"- **{status}** {check}" + (f" — `{detail}`" if detail else "") for status, check, detail in results]
    timings = out / "timings.md"
    if timings.exists():
        lines += ["", timings.read_text()]
    summary = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary)
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
