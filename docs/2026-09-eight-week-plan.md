# Eight-week development plan: 7 September to 1 November 2026

**Starting point.** Qwen Scribe is at `v0.2.1-beta.1` on the releases page and
at `0.2.2` on `main`: the queue, persisted settings, all fourteen languages,
and the CJK timestamp fix have been merged since 7 August but never tagged.
The repository has 212 stars, 6 forks, no issues, no external pull requests,
and a green CI on `main` (92 model-free tests). The release pipeline signs and
notarizes automatically once credentials exist; nothing has been signed yet.

**What this cycle is for.** Three outcomes, in priority order:

1. **Ship what exists and keep shipping.** Release `v0.2.2-beta.1` in week 1,
   then a release every two to three weeks, signed and notarized from the
   first release after the Developer ID arrives (see the revision below).
2. **Turn a developer beta into an app anyone can install.** No Homebrew, no
   Python, no ffmpeg, and one Gatekeeper approval until the Developer ID
   arrives, none after: an `.app` that works after a download, plus a
   Homebrew cask for the people who prefer it.
3. **Make dictation competitive.** Dictation is the surface people compare
   with VoiceInk, Superwhisper, and Wispr Flow. Qwen3-ASR's vocabulary hints
   are the model's edge and are currently switched off for dictation; a
   personal dictionary, a toggle mode, the Fn key, and spoken commands put the
   daily-use path on par without a cloud or an LLM.

Everything else, notably local LLM post-processing, streaming partial results,
and diarization, is deferred with reasons in the last section.

## Revision of 4 September: unsigned builds through this cycle

The Apple Developer Program enrolment is applied for but not paid, and the
owner is not ready to pay yet. Every release in this cycle is therefore
ad-hoc signed and not notarized, as the four betas so far have been, and
each install keeps the one-time **System Settings → Privacy & Security →
Open Anyway** approval. The release pipeline already signs and notarizes
the moment the six secrets from `RELEASING.md` exist, so nothing is lost
by waiting; the first release after the enrolment clears is signed with no
other change, and its notes tell users to re-grant the three permissions
once, because signing changes the app's identity.

What this changes below, with the affected items rewritten in place:

- Week 2's certificate and secrets work moves to "when the enrolment
  clears"; the dry run remains useful and is run against the ad-hoc build.
- Week 6's bundled interpreter is ad-hoc signed inside-out like the rest of
  the bundle. The gating check is no longer notarization but a Mac checks
  run that starts the app with no other Python on the runner's `PATH`, plus
  `codesign --verify --deep --strict` on the result. The hardened runtime
  and the library-validation entitlement are not needed for an ad-hoc
  build and are left for the signed release.
- Week 7's Homebrew cask installs an unsigned app: the cask carries a
  `caveats` block with the Open Anyway steps, and the README's install
  steps make that approval step two of five rather than a footnote.
- Acceptance criteria that said "notarized" now say "after the one-time
  Open Anyway approval". The success list at the end is updated to match.

## Release calendar

| Date | Release | Contents |
| --- | --- | --- |
| Fri 12 Sep | `v0.2.2-beta.1` | Everything already on `main`. Ad-hoc signed unless the Developer ID arrives early |
| Fri 2 Oct | `v0.3.0-beta.1` | Dictation: dictionary, save-to-history opt-out, toggle mode, Fn key, spoken commands, replacements. Launch at login. Model warm-up, idle unload, download progress. **Shipped early on 4 Sep, ad-hoc signed** |
| Fri 23 Oct | `v0.4.0-beta.1` | Quantized models in-app. Bundled Python runtime. AVFoundation decoding without ffmpeg. Check for updates. Homebrew tap. Ad-hoc signed unless the enrolment has cleared |
| Fri 30 Oct | `v0.4.1-beta.1` | Local-path and batch ingest: Open With, drop on the Dock icon, Transcribe Files…, outputs beside the source. Fixes from the week-8 install matrix. Ad-hoc signed unless the enrolment has cleared |

A release slips rather than ships broken. The definition of done for every
release: `make check` green, `make app` verified on a Mac, the changelog
section dated, README status line and the three version fields updated,
the draft's archive installed on a Mac that did not build it, and the manual
dictation check in `RELEASING.md` step 4 performed on that archive.

## Working agreement

- **Claude (this session and its successors)** implements server, web
  interface, native helper, CI, packaging, and documentation changes on
  feature branches, one pull request per task, with tests and changelog
  entries, and opens the pull request with manual-test instructions. The
  macOS CI job compiles `native/DictationHelper.m`, so native changes are at
  least compile-checked before a human runs them.
- **Owner** reviews and merges, runs `make app` and the manual tests on an
  Apple Silicon Mac, owns every Apple account action (enrolment, certificates,
  secrets), tags releases, publishes drafts, and posts announcements. Budget:
  roughly four to six hours a week, front-loaded in weeks 1, 2, 4, 7, and 8.
- **Rules that do not move.** Local-only: no network call outside
  `127.0.0.1` except the ones already documented (PyPI, Hugging Face) and the
  two added here, which are user-initiated and named in `PRIVACY.md` before
  they ship. `HOST` stays `127.0.0.1`. Exact pins. No third-party JavaScript.
  Settings go through the server's validated, atomic store. Every task ends
  with `make check` green.
- **Branch names** follow the existing pattern: `fix/…`, `feat/…`, `ci/…`,
  `docs/…`, `release/vX.Y.Z-beta.N`.

---

## Week 1 (7 to 13 Sep): ship v0.2.2 and unblock the pipeline

**Theme.** Get four weeks of finished work into users' hands and remove the
two things that silently stall the project: the unreleased tag and the
Dependabot dead end.

**Claude**

- [x] `release/v0.2.2-beta.1`: update the README status line, the
      `requirements-lock.txt` header comment, and the `[Unreleased]` link
      block in `CHANGELOG.md`; confirm `macos/QwenScribe-Info.plist` and
      `scripts/build_macos_apps.sh` already say `0.2.2`.
- [x] `fix/dependabot-pydantic-core` (superseded by `ci/uv-lock`, which moves the pair together): bump `pydantic_core` to `2.46.5` next
      to pydantic `2.13.5` on top of Dependabot's PR #18 (or close #18 and
      push the combined bump), so the macOS resolve step passes again. Add a
      comment in `.github/dependabot.yml` that the ignore rule means every
      pydantic bump needs a hand-bumped core until week 2's lock tooling
      lands.
- [x] `ci/dependabot-monthly`: switch the pip group to a monthly schedule.
      Two of the last three bot PRs were closed unmerged; weekly is noise.
- [x] `docs/feedback-channel`: add a `.github/ISSUE_TEMPLATE/testing_report.yml`
      template shaped like the r/macapps report (what you ran, machine,
      what broke, what you expected), and a `FEEDBACK` section in the README
      pointing at it.
- [x] Draft the pinned "v0.2.2 beta: testing thread" issue text for the
      owner to post.

**Owner**

- [ ] Do the still-open v0.1 roadmap item: fresh clone and `make app` on a
      second Apple Silicon Mac, ideally one without Homebrew Python, and note
      what the launcher says. This is the cheapest preview of week 6.
- [ ] Tag `v0.2.2-beta.1`, verify the draft per `RELEASING.md`, publish.
- [ ] Enable GitHub Discussions or pin the testing-thread issue; reply to the
      r/macapps tester who drove v0.2.2 with the release link.
- [ ] **Enrol in the Apple Developer Program today.** Approval takes up to
      48 hours; the certificate and API key are needed in week 2.

**Acceptance.** `v0.2.2-beta.1` is public; PR #18 or its replacement is
merged with green CI; the feedback template exists; Developer Program
enrolment is submitted.

---

## Week 2 (14 to 20 Sep): foundations for v0.3 and the first signed build

**Theme.** The server, the page, and the helper are each a single file of
1,100 to 1,400 lines. That was right for the first betas and will be wrong
under the features of weeks 3 to 8. Split now, while the 92 tests make it
safe, and make dependency updates resolve themselves.

**Claude**

- [x] `refactor/server-package`: move `server.py` into a `qwen_scribe/`
      package with `config.py` (constants, paths, languages, hotkeys),
      `settings.py` (store, validators), `jobs.py` (store, worker, cancel,
      retry), `history.py` (transcripts, search, export), `api.py` (routes,
      middleware), and `app.py` (FastAPI assembly). `server.py` stays as the
      thin entry point so `./run.sh` and the launcher keep working. Update
      `macos/launcher.sh` `prepare_runtime`, `scripts/build_macos_apps.sh`,
      the `Makefile`, `ci.yml`, and `release.yml` to copy and compile the
      package. Tests import the new modules; behaviour is unchanged and every
      existing test passes unmodified in intent.
- [x] `refactor/transcript-js`: extract `sentenceLines` and the SRT cue
      builder from `static/index.html` into `static/transcript.js`, loaded by
      the page (CSP already allows same-origin scripts) and by
      `tests/js/transcript.test.mjs` under Node's built-in `node --test`. Add
      the Node step to `ci.yml`. No dependencies, no build step, in line with
      `CONTRIBUTING.md`.
- [x] `ci/uv-lock`: add `pyproject.toml` and `uv.lock` as the resolution
      source of truth; keep `requirements-lock.txt` as the artifact the
      launcher installs, regenerated with `uv export`, and add a CI step that
      fails when the two drift. Switch Dependabot to the `uv` ecosystem so
      pydantic and pydantic-core move together and the ignore rule can go.
- [x] `ci/macos-real-lock`: the macOS job installs `requirements-lock.txt`
      for real (with a pip cache) and runs the test suite, so the one test
      that is skipped without MLX runs somewhere. Weights are still never
      downloaded.
- [x] `ci/release-dry-run`: add `workflow_dispatch` with a `dry_run` input to
      `release.yml` that builds, signs, notarizes, and uploads the archive as
      a workflow artifact without touching releases. This is how the
      credentials get exercised before a real tag depends on them.

**Owner**

- [ ] When the enrolment clears, whichever week that is: create the
      Developer ID Application certificate and the App Store Connect API
      key; add the six secrets from `RELEASING.md`; run the release dry run
      and install its archive, since a hardened-runtime build that lost the
      audio-input entitlement records silence and only a hands-on check
      catches it. Until then every release stays ad-hoc signed.
- [ ] Run the release dry run on the ad-hoc build once and install its
      archive; it is the same archive the Release workflow produces from a
      tag, so this doubles as the pre-release install check.

**Acceptance.** Package split merged with all tests green on both CI jobs;
Node tests cover the sentence splitter and SRT builder; `uv.lock` drift check
in place; a dry-run archive has been installed and dictates on a Mac.

**Risk.** The hardened runtime is on for signed builds. If Gatekeeper or
TCC behaves differently for the signed helper (permission re-grants are
expected and already documented in the release notes), that is discovered
here rather than on release day.

---

## Week 3 (21 to 27 Sep): dictation I, the daily-use path

**Theme.** Dictation currently sends an empty vocabulary hint
(`native/DictationHelper.m:525`), saves every utterance to history, only
works while a key is physically held, and stops after 120 seconds
(`native/DictationHelper.m:45`). Fix the four things a daily user hits first.

**Claude**

- [x] `feat/dictation-dictionary`: new setting `dictation.dictionary`
      (string, up to 2000 characters, same validator as `context`). The
      helper sends it as the `context` field. Web interface: a textarea in
      the dictation settings with the same copy style as "Domain vocabulary".
      Tests: round trip, limit, helper contract (`/api/settings` shape).
- [x] `feat/dictation-history-optout`: `dictation.save_history` (bool,
      default true). Jobs submitted with `source=dictation` skip
      `_save_transcript` when it is false, report `history_saved: false`
      without an error, and are evicted from the in-memory store 60 seconds
      after the helper collects the result instead of after an hour, so
      dictated text does not linger. `PRIVACY.md` gains one paragraph.
- [x] `feat/dictation-toggle-mode`: `dictation.mode` in `{"hold","toggle"}`
      (default `hold`). In toggle mode a press shorter than 400 ms starts
      recording and the next press stops it; a long press still behaves as
      hold. The cap becomes `dictation.max_seconds` (60 to 600, default 120
      for hold, 600 for toggle) and the HUD shows elapsed time in toggle
      mode. Menu bar gets a Mode submenu. The watchdog and the lost-key-up
      protection stay.
- [x] `feat/model-lifecycle`: three server changes that share one design.
      (1) Download progress: pre-fetch weights with
      `huggingface_hub.snapshot_download` and a tqdm subclass that writes
      "Downloading model 1.2 of 3.4 GB" into the job's `detail` and
      `progress`. (2) Warm-up: after start, and whenever the dictation model
      setting changes, load the dictation model in the worker as a hidden
      job so the first dictation is not the slow one. (3) Idle unload: drop a
      cached session after `performance.unload_after_minutes` (0 to 240,
      default 20, 0 means never) without a job; never while one runs. New
      HUD state "Loading model…" so a reload after idle reads as expected
      rather than as a hang. Tests for the unload timer and the hidden job's
      absence from `/api/jobs`.

**Owner**

- [ ] Manual test on the v0.3.0-beta.1 build: dictionary improves a name,
      toggle mode starts and stops with two taps, history opt-out leaves the
      list untouched, the first dictation after launch is fast, and a
      dictation after 25 idle minutes shows the loading HUD and then works.

**Acceptance.** Four features merged behind settings, all default-safe;
`/api/settings` still accepts the old helper's payload; helper compiled by
CI and hand-tested.

---

## Week 4 (28 Sep to 4 Oct): dictation II and v0.3.0-beta.1

**Theme.** The remaining dictation asks, then the first signed release.

**Claude**

- [x] `feat/fn-hotkey`: an IOHIDManager listener for the physical Fn key
      (Apple vendor top-case usage page, keyboard Fn usage), matched on the
      built-in keyboard and any external Apple keyboard that reports it. Fn
      appears in `DICTATION_HOTKEYS` and the helper table as `fn`, listed as
      unavailable in the menu when no attached keyboard exposes it. Input
      Monitoring is already required, so no new permission. The existing
      modifier path is untouched; the roadmap's PageUp problem does not
      arise because the physical key, not the synthesized flag, is watched.
      **Fallback:** if the listener is not reliable across the owner's
      keyboards by Thursday, ship `v0.3.0` without Fn and carry it to 0.3.1.
- [x] `feat/dictation-cleanups`: server-side, for `source=dictation` jobs
      only. Spoken commands (`dictation.spoken_commands`, default true):
      "new line", "new paragraph", "period", "comma", "question mark" in the
      dictation language when it is English, with a table designed so other
      languages can be added. Replacements (`dictation.replacements`, up to
      100 pairs of up to 100 characters, applied case-insensitively on word
      boundaries). Trailing-whitespace and double-space normalisation.
      Settings UI: a small two-column editor. Tests: each command, boundary
      cases, replacement ordering, and that file transcription is never
      touched by any of it.
- [x] `feat/launch-at-login`: `SMAppService.mainApp` register and unregister
      from a menu item with a check mark that reflects the real state on each
      menu open. Documented note that the app should live in `/Applications`
      for the login item to survive moves.
- [x] `release/v0.3.0-beta.1`: changelog, README (dictation section
      rewritten around dictionary, modes, and keys), `PRIVACY.md` (history
      opt-out, dictionary storage), `ROADMAP.md` (tick the shipped v0.3
      items), version bump to `0.3.0` in the plist and build script.

**Owner**

- [ ] Test Fn on the built-in keyboard and at least one external keyboard;
      decide ship or fallback by Thursday 1 Oct.
- [x] Tag `v0.3.0-beta.1`. Done early, on 4 Sep, from `main` after the Mac
      checks workflow (`.github/workflows/mac-checks.yml`) passed on the
      macOS runner: build, helper self-checks, launcher, and the real 0.6B
      model transcribing English, Korean and Japanese with word timestamps.
      Unsigned, because the Apple Developer enrolment is applied for but not
      paid; signing and notarization move to the first release after it
      clears.
- [ ] Publish the draft release; announce on the r/macapps thread and as a
      Show HN follow-up comment.

**Acceptance.** `v0.3.0-beta.1` is a download that launches without the Open
Anyway hoop once the Developer ID is in place; until then the release notes
carry the unsigned-build instructions from earlier betas.

---

## Week 5 (5 to 11 Oct): speed, quantized models in the app

**Theme.** The Hacker News measurement of roughly 2x realtime on an M1
against 60x for Parakeet is the sharpest criticism the project has received.
The upstream library reports a 4.7x speed-up for the 0.6B model at 4-bit,
and `quantize_8bit.py` already proves the 1.7B 8-bit path. Make quantized
models first-class instead of a developer script.

**Claude**

- [x] `feat/model-catalog`: `GET /api/models` lists every model with `id`,
      `label`, `memory_gb`, `state` in `{"ready","downloadable","needs_conversion","converting"}`.
      Catalog: `0.6b`, `0.6b-4bit`, `1.7b`, `1.7b-8bit`. Quantized variants
      are produced on the device from the upstream fp16 weights (the
      supply-chain stance in `SECURITY.md` stays: weights come from the
      upstream repository), stored under the app data directory, and
      reported with their bits and group size. `MODELS` becomes a function
      of the catalog; the `_QUANT_ACTIVE` shim and the `./models` lookup are
      retired with a migration that adopts an existing `models/qwen3-asr-1.7b-8bit`.
- [x] `feat/model-conversion-job`: `POST /api/models/{id}/prepare` enqueues
      a conversion in the same single worker with progress, cancel, and the
      same idle-unload rules. The picker offers "1.7B 8-bit (fastest
      accurate)" and "0.6B 4-bit (fastest)" with a one-line quality note
      taken from the upstream benchmark table, and a "Prepare" button when
      conversion is needed. Dictation settings get the same picker.
- [x] `docs/model-guide`: a short `docs/models.md` with memory, speed, and
      quality expectations per variant and the `compare_models.py` recipe
      for validating on the user's own recordings; `compare_models.py`
      learns the catalog paths.
- [ ] **Stretch, upstream:** open a pull request to `mlx-qwen3-asr`
      extending its language-name table from 14 towards the 30 languages the
      model supports. When a release lands and the pin moves, the picker
      follows automatically through `test_languages_match_the_installed_model`.
      Not possible from the autonomous session, whose GitHub access is
      scoped to this repository; the owner opens it, or a session with
      access to the upstream repository does.

**Owner**

- [ ] Run `compare_models.py` on three real recordings (quiet, noisy,
      multilingual) for each quantized variant; the word-difference rates go
      into `docs/models.md` as the published expectation.

**Acceptance.** A fresh install can pick "0.6B 4-bit", watch it prepare, and
transcribe with it; memory and speed numbers are documented from real runs.

**Risk.** Quantization quality on non-English audio. Mitigation: the default
model stays fp16 1.7B; quantized variants are opt-in with published numbers.

---

## Week 6 (12 to 18 Oct): self-contained runtime

**Theme.** Today the launcher searches for a Python 3.12 (`macos/launcher.sh`
`find_python`) and fails with a dialog if none exists, and every non-WAV file
needs ffmpeg. Both requirements are invisible to the people who star the
project and fatal to the people who download it.

**Claude**

- [x] `feat/bundled-python`: `scripts/build_macos_apps.sh` downloads a
      pinned, SHA-256-verified `python-build-standalone` 3.12 `install_only`
      build for `aarch64-apple-darwin` into
      `Contents/Resources/Python` — not `Frameworks`, where the first CI run
      showed codesign demanding a signature for every file it reads as
      nested code, which includes the shebang lines the stdlib carries on
      `pdb.py`, `tarfile.py` and two dozen more; under `Resources` they are
      sealed by hash into the app's own signature instead — signs every
      Mach-O inside it (the
      interpreter, `libpython`, stdlib extension modules) inside-out with
      the same identity as the rest of the bundle, ad-hoc until a Developer
      ID exists, and the launcher prefers that interpreter to create the
      venv in Application Support as it does now. For the later signed
      build the entitlements file will need
      `com.apple.security.cs.disable-library-validation`, which a hardened
      interpreter needs to load pip-installed extension modules that Apple
      did not sign with our Team ID; `RELEASING.md` records that now so the
      signed release does not rediscover it. The standard library is
      compiled at build time and the launcher sets
      `PYTHONDONTWRITEBYTECODE`: the archive ships almost no bytecode, and
      the second CI run showed the first import writing `__pycache__` into
      the bundle and breaking the signature it had just verified. Build size
      grows by roughly 30 MB compressed. `find_python` remains as the
      fallback for `./run.sh`.
- [x] `feat/avfoundation-decode`: the helper gains
      `--decode <input> <output.wav>` using `AVAssetReader` to produce
      16 kHz mono 16-bit PCM. The launcher exports the helper's path as
      `QWEN_SCRIBE_DECODER`; the server uses it for every format AVFoundation
      handles (mp3, m4a, aac, mp4, mov, m4v, aiff, flac, qta) and falls back
      to ffmpeg only for mkv, webm, ogg, opus, and wma. The "ffmpeg missing"
      messages shrink to those five formats. Tests cover decoder selection
      by suffix and the error path when neither decoder exists. The third
      CI run showed the cost of a helper no Linux check can compile: the
      sample-buffer functions are CoreMedia's, the link line named every
      other framework, and all three macOS jobs died at the same step. A
      table of the frameworks that provide the functions the source calls
      now stands in for the compiler that is not there.
- [x] `ci/bundled-runtime-check`: the Mac checks workflow gains a job that
      starts the built app with every other Python removed from `PATH` and
      Homebrew's directories hidden, proving the launcher creates the
      environment from the bundled interpreter, and `codesign --verify
      --deep --strict` passes on the result. This is the week's gating
      check and runs on Monday, before the rest of the work depends on it.
      Notarization of the bundled interpreter is checked by the release dry
      run only once the enrolment has cleared.

**Owner**

- [ ] Install the resulting build on the Mac without Homebrew from week 1:
      confirm Open Anyway approves the bundle once, the app starts with the
      bundled interpreter, and an `.m4a` and an `.mp4` transcribe with
      ffmpeg absent.

**Acceptance.** A Mac with no Homebrew, no Python, and no ffmpeg downloads
the archive, approves it once under Open Anyway, opens the app, and
transcribes an `.mp4`.

**Risk.** Gatekeeper's treatment of a large ad-hoc bundle with a nested
runtime is the largest technical unknown left in the plan: the Open Anyway
approval covers the whole bundle, but every nested Mach-O must carry a
consistent signature or macOS reports the app as damaged. Mitigation: the
Monday CI check verifies the deep signature, and the fallback is a runtime
download on first launch (pinned URL, verified hash, documented in
`PRIVACY.md`).

---

## Week 7 (19 to 25 Oct): distribution and v0.4.0-beta.1

**Theme.** Everything a newcomer touches between "I heard of this" and "it
works", plus the release.

**Claude**

- [ ] `feat/check-for-updates`: a "Check for Updates…" menu item in the
      helper that, only when clicked, fetches the latest release from the
      GitHub API, compares the tag with the bundle version, and opens the
      release page if newer. No automatic checks. `PRIVACY.md` names the
      request and what it carries (nothing but the request itself).
- [ ] `dist/homebrew-tap`: a `Casks/qwen-scribe.rb` for a new
      `VladUZH/homebrew-tap` repository with `version`, `sha256`, both apps,
      a `zap` stanza listing the Application Support, Logs, and cache paths
      from the README, `livecheck` against GitHub releases, and a `caveats`
      block with the Open Anyway steps, since the cask installs an unsigned
      app and the download still carries the quarantine attribute. People
      who prefer to skip the prompt can pass `--no-quarantine` themselves;
      the cask does not do it for them. The release workflow gains a final
      step that opens a pull request to the tap with the new version and
      checksum.
- [ ] `docs/readme-for-users`: rewrite the README's top half for someone who
      does not build software, in five steps: download, approve once under
      Open Anyway, open, grant three permissions, hold the key. Move
      build-from-source, environment variables, and architecture below a
      "For developers" line. Refresh `docs/assets` screenshots to the
      current interface, including the Open Anyway prompt. Add an "Install
      matrix" section to `RELEASING.md`.
- [ ] `release/v0.4.0-beta.1`: changelog, version bump, roadmap ticks
      (self-contained runtime and quantized models are new roadmap lines and
      are recorded as shipped).

**Owner**

- [ ] Run the install matrix on Thursday: macOS 14 and 15 (and 26 if
      available), with and without Homebrew, fresh permissions each time.
      Findings become issues tagged `v0.4.1`.
- [ ] Create the `homebrew-tap` repository; tag `v0.4.0-beta.1` on Friday 23
      Oct; publish; merge the tap PR the workflow opens.

**Acceptance.** `brew install --cask vladuzh/tap/qwen-scribe` installs the
app; after the one-time Open Anyway approval it works without any other
prerequisite.

---

## Week 8 (26 Oct to 1 Nov): local paths, batch, and v0.4.1-beta.1

**Theme.** The roadmap's workspace idea, in its smallest useful form: let
files reach the queue without a browser upload. This removes the double-disk
staging behind the 4 GB cap for those files, gives Finder integration, and
handles a folder of recordings at once.

**Claude**

- [ ] `feat/local-path-jobs`: `POST /api/jobs/local` with `{"paths": [...]}`.
      Each path must be a regular, readable file with an allowed suffix; a
      job is created per path with `local: true`, no staging copy, the
      source never deleted, and retry re-referencing the path instead of
      copying. Directories are expanded one level, allowed suffixes only,
      capped at 200 files per request. Trust boundary unchanged and stated
      in `SECURITY.md`: same user, same machine, localhost only.
- [ ] `feat/finder-integration`: `CFBundleDocumentTypes` for audio and video
      UTIs so the app appears under Open With and accepts drops on its Dock
      icon; `application:openURLs:` posts the paths to the local endpoint and
      opens the web interface on the queue. A "Transcribe Files…" menu item
      opens an `NSOpenPanel` with multiple selection and folders allowed.
- [ ] `feat/outputs-beside-source`: `transcription.write_beside_source`
      (bool, default false) writes `.txt` and, when timestamps exist, `.srt`
      next to each local source on completion, never overwriting an existing
      file (numbered suffix). The queue row shows where the outputs went.
- [ ] `release/v0.4.1-beta.1`: install-matrix fixes from week 7, the three
      features above, changelog, version bump.
- [ ] `docs/next-cycle`: a retrospective (what shipped, what slipped, what
      users said) and the plan for the following eight weeks, seeded from the
      deferred list below.

**Owner**

- [ ] Drop a folder of 20 recordings on the Dock icon; confirm the queue
      order, cancellation of a few, and outputs beside the sources.
- [ ] Tag `v0.4.1-beta.1` on Friday 30 Oct; publish; announce.

**Acceptance.** A folder of recordings becomes a queue without the browser;
a 6 GB screen recording transcribes from its path with no staged copy.

---

## Deferred beyond this cycle, with reasons

- **Local LLM post-processing** (summarise, translate, reformat). The
  deterministic cleanups in week 4 are the on-ramp and settle the settings
  and pipeline shape. An LLM adds a second multi-gigabyte model in unified
  memory and a new class of failure; it deserves its own cycle after the
  runtime is self-contained and the memory lifecycle from week 3 has been in
  users' hands.
- **Streaming partial dictation.** Upstream supports KV-cache streaming, so
  it is feasible, but it changes the helper's upload-then-poll contract and
  the HUD. Best done after toggle mode has shown how people actually dictate
  long passages.
- **Speaker diarization.** The upstream path pulls in PyTorch and a gated
  Hugging Face model that needs an account token. Both contradict the
  no-account promise that the privacy-minded audience values. Revisit only if
  a token-free, MLX-native path appears.
- **Streaming or chunked upload to lift the 4 GB browser cap.** Week 8's
  local-path ingest covers the multi-hour-video case for app users; the
  browser upload path can stay capped.
- **Localization and the accessibility audit.** Worth doing once the
  interface stops changing weekly; the week 7 README rewrite and screenshots
  are the natural moment to plan it.
- **Developer ID signing and notarization.** Not deferred by choice but by
  the enrolment's timing, and not blocking anything: `release.yml` signs
  and notarizes as soon as the secrets exist. What the signed release will
  need is written down where it belongs so it is not rediscovered: the
  library-validation entitlement for the bundled interpreter (week 6), the
  hands-on microphone check of a hardened-runtime build (week 2), and the
  note in that release's notes that users re-grant the three permissions
  once because the app's identity changes.

## Owner action checklist, dated

| By | Action |
| --- | --- |
| Thu 3 Sep | Enrol in the Apple Developer Program (applied; payment deferred, see the revision above) |
| Fri 11 Sep | Fresh-clone test on a second Mac; pin the testing thread (`v0.2.2` shipped inside `v0.3.0-beta.1` on 4 Sep) |
| Fri 18 Sep | Dry-run archive installed and mic-checked. Certificate, API key and the six secrets whenever the enrolment clears |
| Fri 25 Sep | Week 3 manual tests on the dry-run build |
| Thu 1 Oct | Fn key ship-or-fallback decision |
| Fri 2 Oct | Announce `v0.3.0-beta.1` (published 4 Sep) |
| Fri 9 Oct | `compare_models.py` runs on three recordings per variant |
| Mon 12 Oct | Bundled-runtime CI check green; no-Python Mac test scheduled |
| Fri 16 Oct | No-Homebrew Mac test of the self-contained build |
| Thu 22 Oct | Install matrix |
| Fri 23 Oct | Create the tap repository; tag and publish `v0.4.0-beta.1` |
| Fri 30 Oct | Folder-drop test; tag and publish `v0.4.1-beta.1` |

## What success looks like on 1 November

- Four releases in eight weeks, ad-hoc signed until the Developer ID
  arrives; the pipeline signs and notarizes the first release after it
  does, with no other change.
- A first-time user with a bare Mac installs from a download or a cask,
  approves the app once under Open Anyway, and dictates within five
  minutes, without a terminal.
- Dictation uses the personal dictionary, supports toggle mode and the Fn
  key, and can be kept out of history.
- The 0.6B 4-bit model is available in the picker with published speed and
  quality numbers from real recordings.
- Feedback arrives as issues in the repository rather than only on Reddit
  and Hacker News.
- Dependabot pull requests merge on their own when CI is green.
