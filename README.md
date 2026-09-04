<p align="center">
  <img src="assets/AppIcon-1024.png" width="118" alt="Qwen Scribe app icon">
</p>

<h1 align="center">Qwen Scribe</h1>

<p align="center"><strong>Private, local transcription and system-wide dictation for Apple Silicon.</strong></p>

<p align="center">
  <a href="https://github.com/VladUZH/qwen-scribe/actions/workflows/ci.yml"><img src="https://github.com/VladUZH/qwen-scribe/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status"></a>
  <a href="https://github.com/VladUZH/qwen-scribe/releases"><img src="https://img.shields.io/github/v/release/VladUZH/qwen-scribe?include_prereleases&amp;sort=semver&amp;label=release" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="Apache-2.0 license"></a>
  <img src="https://img.shields.io/badge/platform-macOS%2014%2B%20%7C%20Apple%20Silicon-lightgrey" alt="macOS 14 or newer on Apple Silicon">
  <img src="https://img.shields.io/badge/data-100%25%20on--device-brightgreen" alt="All processing happens on-device">
</p>

Qwen Scribe transcribes audio and video with
[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) running through
[mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr) on the Mac's Metal
GPU. There is no account, API key, or Qwen Scribe cloud service. Audio and
transcript text stay on the Mac.

> **Project status:** `v0.3.0-beta.1`. Core transcription, history, and
> dictation work. A downloadable beta build exists on the
> [releases page](https://github.com/VladUZH/qwen-scribe/releases), where its
> provenance notes state the exact signing and notarization status. An ad-hoc
> build is blocked on first launch until you approve it under **System Settings
> → Privacy & Security → Open Anyway**; building from source with `make app`
> avoids that hoop. Developer ID-signed and notarized binaries follow once
> credentials are configured
> ([RELEASING.md](RELEASING.md), [ROADMAP.md](ROADMAP.md)). Feedback and
> careful testing are welcome.

<p align="center">
  <img src="docs/assets/hero.png" width="900" alt="Qwen Scribe interface showing local transcription options, a synthetic transcript, and saved transcript history">
</p>

<p align="center"><sub>The interface above uses synthetic demo text; no private recording or transcript is shown.</sub></p>

## Highlights

- Drag-and-drop audio and video transcription in a focused local web interface
- Qwen3-ASR 1.7B for accuracy or 0.6B for speed
- Automatic language detection, optional forced language, vocabulary hints,
  word timestamps, and SRT export
- Automatic local transcript history with search, reopen, export-all,
  individual delete, and delete-all controls
- Hold the configured **push-to-talk key** (right Command by default; right
  Option, right Control, or the Fn/Globe key if you prefer) in any text
  field, speak, then release to transcribe locally and paste at the cursor.
  Or switch to
  press-to-start, press-to-stop for longer dictation, with the HUD counting
  the seconds
- Menu-bar status item with the dictation state, a push-to-talk key picker,
  and explicit restart and quit controls
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
- **No Python needed for the app.** It carries its own interpreter, so a Mac
  with nothing installed can run it. Python 3.12 or newer is needed only to
  run from source with `./run.sh` — the floor set in
  [pyproject.toml](pyproject.toml) and required by the pinned NumPy in
  [requirements-lock.txt](requirements-lock.txt). If you do have a suitable
  Python and prefer it, a build made with `BUNDLE_PYTHON=0 make app` uses it
  instead.
- `ffmpeg` only for Matroska, WebM, Ogg, Opus and WMA. Everything else —
  mp3, m4a, aac, aiff, flac, mp4, mov, m4v, Voice Memos — the app decodes
  itself through AVFoundation, so a Mac with no Homebrew transcribes it. For
  those five, install ffmpeg with Homebrew (`brew install ffmpeg`) or
  MacPorts (`sudo port install ffmpeg`); the app looks on the standard
  Homebrew and MacPorts paths and then on your login shell's `PATH`. Running
  from source with `./run.sh` has no helper, so there ffmpeg still covers
  every non-WAV file.
- Apple Command Line Tools to build the app from source:
  `xcode-select --install`

The 1.7B model needs roughly 3.4 GB of unified memory and the 0.6B model roughly
1.2 GB, in addition to normal application overhead; the quantized variants the
picker can prepare on your Mac need about 1.9 GB and 0.5 GB
([docs/models.md](docs/models.md)). Model weights are not included in this
repository; the first use downloads them, with the progress shown on the
job. A loaded model is released after 20 minutes without a job,
and the dictation model is loaded when the app starts so the first dictation is
quick. Both are in **Settings → Advanced**, behind the gear in the corner.

Word timestamps for Japanese and Korean use the `nagisa` and `soynlp`
tokenizers from the upstream `aligner` extra. The runtime lock has included
them since v0.2.2, so they install with everything else; if the aligner still
cannot run on a given file, the transcript is produced and saved without the
`.srt`, and the interface says why.

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

**Launch at Login** in the menu bar registers the app as a login item through
macOS's own login-item service. The item points at the bundle's location, so
keep the app in `/Applications`; a rebuild into `dist/` would otherwise leave
it pointing at a bundle that has moved. macOS may ask you to allow the item
under **System Settings → General → Login Items** the first time.

An app built locally is ad-hoc signed. If Gatekeeper blocks a downloaded build,
open it once, then approve it under **System Settings → Privacy & Security →
Open Anyway** (macOS 15 removed the right-click **Open** bypass). Stable public binaries should be signed
with an Apple Developer ID and notarized; see [ROADMAP.md](ROADMAP.md).

Server logs are written to `~/Library/Logs/QwenScribe.log`.

### Terminal alternative

```bash
./run.sh
```

Then open <http://127.0.0.1:8990>. The first transcription downloads the
selected model once. Subsequent use works offline once dependencies and weights
are cached.

## Limits and controls

Qwen Scribe is a **menu-bar app plus a local web interface**. The waveform icon
at the right of the menu bar carries Open, Restart Server, and Quit; the browser
page at <http://127.0.0.1:8990> is the interface for file transcription,
history, and settings. Closing the browser tab does not stop anything — quit
from the menu bar (or with `Stop Qwen Scribe.app`). Desktop dictation needs no
browser tab open at all.

| | |
| --- | --- |
| Maximum file size | **4 GB.** The upload is staged in a temporary folder before decoding, so one job briefly needs twice the file's size in free disk space. This is not a model limit — for a longer video, extract the audio track first: `ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 output.wav` |
| Concurrency | One file at a time. Drop several files at once, or more while one is running, and they queue in order; the Queue list shows that order, and each entry can be cancelled, a failed one retried without re-uploading. Cancelling a waiting file is immediate; cancelling the one being transcribed takes effect when the current 30-second chunk finishes, since a model call cannot be interrupted part-way |
| Vocabulary hints | Up to 2000 characters, in **Settings → Transcription**. The hint is added to every chunk's prompt, so it is paid for once per chunk of the file. While one is set, the page says so above the dictation card, and that label opens the field |
| Dictation settings | The push-to-talk key, mode, longest recording, model, language, dictionary, and history choice live in **Settings → Dictation** — the gear in the corner, or **Set up** on the dictation card — and apply within about ten seconds. In **Hold to talk** the key records while held; in **Press to start, press to stop** a tap starts and the next tap stops, while a press held longer than a tap still works as hold. A recording is stopped on its own at the configured limit, two minutes by default and ten at most. The dictionary is names and terms you dictate often; it is sent with every dictation as the model's vocabulary hint. Switch off **Save dictations to history** to keep dictations out of the saved transcripts entirely. Say "new line" or "new paragraph" on its own, between sentences, to get a break; **Replacements** turn a phrase you say into the text you want pasted, whole words only, so "my email" can become your address. These are separate from the model, language, and vocabulary used for file transcription |
| Languages | Automatic detection, or any of the fourteen Qwen3-ASR supports. Word timestamps for Japanese and Korean use the tokenizers shipped in `requirements-lock.txt`; if the aligner cannot run, the transcript is still produced and saved, without the `.srt` |

## Desktop dictation permissions

Desktop dictation starts with the Mac app. On first launch, allow Qwen Scribe in
**System Settings → Privacy & Security** for:

- **Microphone** — records while the push-to-talk key is held
- **Input Monitoring** — detects that key while another app is active
- **Accessibility** — pastes the finished text into the focused field

After changing Input Monitoring or Accessibility, stop and reopen Qwen Scribe.
File transcription remains available when these optional permissions are not
granted.

The helper watches modifier-change events and reacts only to the configured
push-to-talk key (right Command unless you change it in the web interface or
the menu bar). The Fn key is watched as a physical key through the keyboard's
own reports, because macOS synthesizes Fn around every arrow and navigation
key; the menu bar offers it only while a keyboard with an Fn key is attached. It snapshots the pasteboard in memory, pastes the transcription, and
restores the previous pasteboard if it has not changed. Without Accessibility
access it cannot insert text at all, so it leaves the transcript on the
clipboard and reports the failure instead of claiming success. See
[PRIVACY.md](PRIVACY.md) for the complete data and permission behavior.

## Local files and deletion

| Data | Default location |
| --- | --- |
| Saved transcripts | `~/Library/Application Support/Qwen Scribe/transcripts` |
| Settings | `~/Library/Application Support/Qwen Scribe/settings.json` |
| Private runtime | `~/Library/Application Support/Qwen Scribe/runtime` |
| Diagnostic log | `~/Library/Logs/QwenScribe.log` |
| Downloaded models | `~/.cache/huggingface/hub` |
| Quantized variants prepared on this Mac | `~/Library/Application Support/Qwen Scribe/models` |

Saved transcripts are readable, unencrypted JSON. Use the history controls to
delete one or all. Normal filesystem deletion is not secure erasure, and local
backups may retain copies.

### Environment variables

These are read by `server.py`, so they apply to `./run.sh` and to the server the
Mac app starts:

| Variable | Default | Purpose |
| --- | --- | --- |
| `QWEN_SCRIBE_DATA_DIR` | `~/Library/Application Support/Qwen Scribe` | Where transcript history is stored |
| `QWEN_SCRIBE_MODEL_DIR` | `~/Library/Application Support/Qwen Scribe/models` | Where quantized variants prepared on this Mac are stored |
| `QWEN_SCRIBE_PORT` | `8990` | Port for the local server |

`QWEN_SCRIBE_PORT` only applies when you start the server yourself with
`./run.sh`. The Mac app and the desktop dictation helper both address
`127.0.0.1:8990` directly, so dictation will not find a server on another port.

## Development

```bash
make setup       # create .venv and install the reviewed dependency lock
make test        # run lightweight API/history tests without model weights
make check       # tests, source compilation, and publication hygiene
make lock        # re-resolve pyproject.toml with uv and export requirements-lock.txt
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

### Quantized models

The model picker offers **1.7B 8-bit** and **0.6B 4-bit** next to the two
upstream models. Either is made on your Mac from the upstream weights when
you press **Prepare** — in **Settings → Models**, which lists every model
with its state, or on the page itself when you pick one that is not ready —
a few minutes once, and is stored under
`~/Library/Application Support/Qwen Scribe/models`.
[docs/models.md](docs/models.md) says what to expect from each. From a
terminal the same conversion is `python quantize_8bit.py [1.7b-8bit|0.6b-4bit]`,
and `python compare_models.py recording.m4a` measures speed and word
differences against the fp16 model on your own recordings. Validate names
and numbers on representative recordings before relying on a quantized model.

## Architecture

```text
Browser UI ──localhost──> FastAPI job queue ──> MLX / Qwen3-ASR
                                  │
                                  └──> local JSON transcript history

Push-to-talk ──> native macOS helper ──> temporary WAV ──> same job queue
        focused app <── restored clipboard + Command-V <── transcript
```

The API serializes GPU jobs to avoid memory contention. Media is staged under a
random temporary filename and removed when processing finishes.

## Feedback

Testing reports are what drive releases: the whole of v0.2.2 came from one
person who ran real video files through the app and wrote down what happened.
If you try Qwen Scribe, please file a
[testing report](https://github.com/VladUZH/qwen-scribe/issues/new?template=testing_report.yml)
with what you ran, on which Mac, and what did or did not work. Bugs and
feature requests have their own templates. Remove private transcript text and
paths before posting.

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
