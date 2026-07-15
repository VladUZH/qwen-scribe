<p align="center">
  <img src="assets/AppIcon-1024.png" width="118" alt="Qwen Scribe app icon">
</p>

<h1 align="center">Qwen Scribe</h1>

<p align="center"><strong>Private, local transcription and system-wide dictation for Apple Silicon.</strong></p>

Qwen Scribe transcribes audio and video with
[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) running through
[mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr) on the Mac's Metal
GPU. There is no account, API key, or Qwen Scribe cloud service. Audio and
transcript text stay on the Mac.

> **Project status:** `v0.1-beta`. Core transcription, history, and dictation
> work, but downloadable app builds are not yet Developer ID signed or
> notarized. Feedback and careful testing are welcome.

<p align="center">
  <img src="docs/assets/hero.png" width="900" alt="Qwen Scribe interface showing local transcription options, a synthetic transcript, and saved transcript history">
</p>

<p align="center"><sub>The interface above uses synthetic demo text; no private recording or transcript is shown.</sub></p>

## Highlights

- Drag-and-drop audio and video transcription in a focused local web interface
- Qwen3-ASR 1.7B for accuracy or 0.6B for speed
- Automatic language detection, optional forced language, vocabulary hints,
  word timestamps, and SRT export
- Automatic local transcript history with reopen, individual delete, and
  delete-all controls
- Hold **right Command** in any text field, speak, then release to transcribe
  locally and paste at the cursor
- A non-focus-stealing HUD for **Listening**, **Transcribing**, success, and
  failure states
- Localhost-only API with Host and browser-origin checks

<p align="center">
  <img src="docs/assets/dictation-states.png" width="900" alt="Qwen Scribe dictation flow: Listening, Transcribing, then Text inserted">
</p>

## Requirements

- Apple Silicon Mac
- macOS 14 or newer, as required by current
  [MLX releases](https://ml-explore.github.io/mlx/build/html/install.html)
- Native Python 3.10 or newer
- `ffmpeg` for non-WAV audio and video: `brew install ffmpeg`
- Apple Command Line Tools to build the app from source:
  `xcode-select --install`

The 1.7B model needs roughly 3.4 GB of unified memory and the 0.6B model roughly
1.2 GB, in addition to normal application overhead. Model weights are not
included in this repository.

## Build and run the Mac app

From a downloaded or cloned source directory:

```bash
make app
open "dist/Qwen Scribe.app"
```

The generated app is self-contained except for Python dependencies and model
weights, which it installs or downloads on first use. You may move both apps
from `dist/` to `/Applications` after building:

- `Qwen Scribe.app` starts the server and desktop dictation helper.
- `Stop Qwen Scribe.app` stops only processes started by Qwen Scribe.

An app built locally is ad-hoc signed. If Gatekeeper blocks a downloaded build,
right-click it and choose **Open** once. Stable public binaries should be signed
with an Apple Developer ID and notarized; see [ROADMAP.md](ROADMAP.md).

Server logs are written to `~/Library/Logs/QwenScribe.log`.

### Terminal alternative

```bash
./run.sh
```

Then open <http://127.0.0.1:8990>. The first transcription downloads the
selected model once. Subsequent use works offline once dependencies and weights
are cached.

## Desktop dictation permissions

Desktop dictation starts with the Mac app. On first launch, allow Qwen Scribe in
**System Settings → Privacy & Security** for:

- **Microphone** — records while right Command is held
- **Input Monitoring** — detects that modifier while another app is active
- **Accessibility** — pastes the finished text into the focused field

After changing Input Monitoring or Accessibility, stop and reopen Qwen Scribe.
File transcription remains available when these optional permissions are not
granted.

The helper watches modifier-change events and reacts only to the right Command
key. It snapshots the pasteboard in memory, pastes the transcription, and
restores the previous pasteboard if it has not changed. See [PRIVACY.md](PRIVACY.md)
for the complete data and permission behavior.

## Local files and deletion

| Data | Default location |
| --- | --- |
| Saved transcripts | `~/Library/Application Support/Qwen Scribe/transcripts` |
| Private runtime | `~/Library/Application Support/Qwen Scribe/runtime` |
| Diagnostic log | `~/Library/Logs/QwenScribe.log` |
| Downloaded models | `~/.cache/huggingface/hub` |

Saved transcripts are readable, unencrypted JSON. Use the history controls to
delete one or all. Normal filesystem deletion is not secure erasure, and local
backups may retain copies.

## Development

```bash
make setup       # create .venv and install the reviewed dependency lock
make test        # run lightweight API/history tests without model weights
make check       # tests, source compilation, and publication hygiene
make app         # build ad-hoc-signed app bundles in dist/
make package     # create a beta release zip in dist/
```

`make app` compiles `native/DictationHelper.m` as the arm64 bundle executable,
embeds the server launcher and tracked sources as signed resources, validates
its property lists, signs it, and verifies the result. Keeping the
privacy-sensitive recorder as the bundle executable gives macOS one stable app
identity for Accessibility, Input Monitoring, and Microphone access. Set
`CODESIGN_IDENTITY` to a Developer ID identity for a release build; notarization
is intentionally a separate release-owner step.

### Optional 8-bit conversion

For source-based use, a local 8-bit conversion can reduce decoding latency:

```bash
source .venv/bin/activate
python quantize_8bit.py
python compare_models.py path/to/representative-recording.m4a
```

This creates a multi-gigabyte `models/` directory that is intentionally ignored
by Git. Validate names and numbers on representative recordings before relying
on a quantized model.

## Architecture

```text
Browser UI ──localhost──> FastAPI job queue ──> MLX / Qwen3-ASR
                                  │
                                  └──> local JSON transcript history

Right Command ──> native macOS helper ──> temporary WAV ──> same job queue
        focused app <── restored clipboard + Command-V <── transcript
```

The API serializes GPU jobs to avoid memory contention. Media is staged under a
random temporary filename and removed when processing finishes.

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes. Please report
vulnerabilities according to [SECURITY.md](SECURITY.md), not in a public issue.
The planned release sequence is documented in [ROADMAP.md](ROADMAP.md).

Project source and issue tracking live at
[VladUZH/qwen-scribe](https://github.com/VladUZH/qwen-scribe).

## License and project identity

Qwen Scribe is licensed under [Apache-2.0](LICENSE). Third-party licenses and
attribution are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Qwen Scribe is an independent community project. It is not affiliated with,
endorsed by, or sponsored by Alibaba Cloud, the Qwen team, or Apple Inc. Qwen
and other names may be trademarks of their respective owners.
