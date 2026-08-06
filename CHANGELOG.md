# Changelog

All notable changes will be documented here. The project follows Semantic
Versioning after `1.0.0`; beta versions may still change setup and storage
details when clearly documented.

## [Unreleased]

## [0.2.2-beta.1] - 2026-08-07

Everything in this release comes from one hands-on testing report on r/macapps
from someone who ran real video files through v0.2.1-beta.1.

### Added

- A visible transcription queue. Every job is listed in the order the worker
  will reach it, with its position while it waits, a Cancel button that stops
  a queued or running job, Retry for a failed one that does not need the file
  uploaded again, and Open for a finished one. Cancelling a running job says
  it is finishing the current chunk, because a model call cannot be
  interrupted part-way; cancelling a waiting one takes effect at once
- A "one sentence per line" toggle on the transcript, so a long result is not
  one unbroken wall of text. Copy text and Download .txt follow the view, and
  the choice is remembered
- The file-transcription choices — model, language, word timestamps, turbo,
  and vocabulary hints — are now remembered. They are stored by the server
  alongside the dictation settings, so they survive a reload, a restart, a
  cleared browser cache, and a different browser
- All fourteen languages Qwen3-ASR supports are now selectable — Arabic,
  Chinese, Dutch, Hindi, Japanese, Korean, Portuguese, and Turkish join the
  six that were already listed

### Changed

- The 4 GB per-file cap is now stated on the drop zone and in the README, and
  the error explains that it protects against a staged upload needing twice
  the file's size in free disk space rather than being a model limit
- The web interface now says that Qwen Scribe is a menu-bar app, and that Open,
  Restart Server, and Quit live there — a tester reported being unable to find
  any way to quit or restart it
- Vocabulary hints are capped at 2000 characters on upload, matching the limit
  already applied to the stored setting; the hint is prepended to every chunk's
  prompt, so its cost is paid once per chunk for the whole file

### Fixed

- Word timestamps and SRT export now work for Japanese and Korean audio. The
  forced aligner needs the `nagisa` and `soynlp` tokenizers for those two
  languages; they were never installed, and the error told users to run a
  `pip install` that could not reach the app's own private environment
- A word-timestamp failure no longer destroys the transcription. The chunk is
  retried without timestamps, the finished text is kept and saved to history,
  and the transcript view explains why there is no `.srt`. That retry is also
  what tells a timestamp failure apart from a failing decoder: when it fails
  too, the job reports the real error rather than blaming the timestamps
- Retrying a large file no longer freezes the interface. The upload was
  copied while holding the lock that also serialises progress updates, the
  queue view, new uploads, and shutdown, so retrying a multi-gigabyte video
  stalled all of them for the length of the copy. Retry is also idempotent
  now: a double click queues one job, not two
- A job cancelled just as its last chunk finished could still complete and
  save itself to history after the interface had already reported it stopping
## [0.2.1-beta.1] - 2026-08-02

### Fixed

- Speculative decoding now reuses the cached 0.6B model instead of loading a
  second copy into unified memory
- Queued uploads are removed during shutdown, worker-submission failures clean
  up immediately, and completed jobs are pruned as they finish
- Long Chinese, Japanese, and Korean transcripts no longer gain spaces at
  chunk boundaries; history search now handles Unicode case folding, and
  invalid numeric dates no longer break the history API
- Browser polling is single-flight, a newer upload cancels the older request,
  and submissions wait for the server-owned language configuration
- Quit and Stop requests end active microphone capture before waiting for the
  model server; delayed paste retries now revalidate focus, modifiers, and the
  clipboard before reporting success; the privacy prompt reflects the
  configurable hotkey
- Release tooling validates versions before destructive paths, preserves the
  submitted archive until a notarized replacement is verified, and refuses to
  overwrite already-published GitHub release assets; beta tags now create
  GitHub releases marked as pre-releases
- Privacy and security documentation now reflects the configurable
  push-to-talk key and the currently supported beta line

### Changed

- Speculative decoding is labeled experimental because the pinned upstream
  implementation currently benchmarks slower than normal decoding
- Documented that Japanese and Korean word timestamps need optional upstream
  tokenizer dependencies that are not yet included in the runtime lock

## [0.2.0-beta.1] - 2026-07-30

### Added

- Menu-bar status item with live dictation state, a push-to-talk key picker,
  and explicit Open, Restart Server, and Quit controls
- In-app dictation settings — push-to-talk key (right Command, right Option,
  or right Control), model, and language — persisted by the server and
  applied by the desktop helper within seconds
- Search across saved transcripts, matching filenames and full text
- Export of every saved transcript as one zip of plain-text files plus the
  complete JSON
- Tag-triggered release workflow that drafts a GitHub release with SHA-256
  checksums and provenance notes, and signs with a Developer ID when
  repository secrets are configured; `scripts/notarize.sh` and `RELEASING.md`
  cover the release-owner steps

### Changed

- The dictation card in the web interface shows the configured push-to-talk
  key instead of assuming right Command

## [0.1.0-beta.1] - 2026-07-29

First public beta. This is a source-only release: build the Mac apps locally
with `make app`. Signed and notarized binaries are planned for v0.2.

### Added

- Local audio and video transcription with Qwen3-ASR on MLX, using the 1.7B
  model for accuracy or the 0.6B model for speed
- Automatic language detection, optional forced language, vocabulary hints,
  word timestamps, and SRT export
- Automatic transcript history with reopen, individual delete, and delete-all
  controls
- System-wide hold-right-Command dictation with clipboard restoration
- Floating listening, transcribing, completion, and error HUD
- Reproducible source, app-bundle, and beta-archive build commands
- Repository publication checks and GitHub Actions CI
- Privacy, security, contribution, conduct, license, and third-party notices
- Local Host-header, cross-origin request, and browser security-header protection

### Fixed

- The Mac app now finds ffmpeg installed with MacPorts (/opt/local/bin) and,
  failing that, anywhere on the login shell's PATH — Finder launches previously
  saw only the Homebrew paths, so MacPorts users were told ffmpeg was missing
- Error messages and docs no longer assume Homebrew is the only way to install
  ffmpeg
- Right Command is now detected by its device-specific modifier bit, so holding
  the other Command key no longer swallows the key release and leaves dictation
  recording indefinitely
- Dictation no longer claims "Text inserted" when Accessibility access is
  missing; the transcript is left on the clipboard and the failure is reported
- Desktop dictation reports a restarted server instead of freezing for ten
  minutes, and cleans up its recording when stopped with SIGTERM
- The browser stops polling a job the server no longer has, and overlapping
  uploads can no longer strand a polling timer that re-renders the page forever
- Re-selecting the same file in the picker starts a new transcription again
- A partial transcript stays readable and exportable when a job fails part-way
- Subtitle export no longer inserts a space between every character for
  Chinese, Japanese, Korean, and Thai
- A damaged transcript file no longer makes the entire history return 500
- Transcript writes are flushed before the atomic rename, and a failed write no
  longer leaves an invisible `.json.tmp` orphan behind
- Deleting a transcript also drops the finished job, which was still serving the
  deleted text from memory
- Finished jobs are evicted, so a long-running server no longer retains every
  transcript it has ever produced in memory
- Stopping the server no longer silently runs the whole queue to completion
- Oversized uploads are refused before the request body is written to disk, and
  a rejected upload no longer leaves a partial temporary file
- `Stop Qwen Scribe` confirms the process actually exited instead of reporting
  success and leaving an orphan
- `make setup` replaces an environment broken by a Python upgrade instead of
  skipping installation and leaving it empty
- Release archives include `NOTICE`, as Apache-2.0 section 4(d) requires, and
  are named after the version of the app bundle they actually contain
- Made the native recorder the app bundle's declared main executable so macOS
  Accessibility, Input Monitoring, and Microphone permissions persist across
  normal stop/start cycles

### Security

- Removed the test-only `testserver` hostname from the production Host
  allow-list, which had left a permanent hole in the DNS-rebinding check
- Rejected requests now carry the same security headers as accepted ones

### Changed

- Documented minimum Python is now 3.12, matching what the pinned dependency
  lock can actually install; it previously promised 3.10
- The web interface takes its supported-format list from the server instead of
  a hardcoded copy that had drifted
- Added reviewed direct dependencies and a fully resolved runtime lock
- Removed remote web-font requests in favor of macOS system fonts
- Raised the documented and bundled minimum to macOS 14 for current MLX
- Mac app builds now create their own private environment rather than copying a
  developer `.venv`
- Model weights and generated app bundles are explicitly excluded from source

[Unreleased]: https://github.com/VladUZH/qwen-scribe/compare/v0.2.2-beta.1...HEAD
[0.2.2-beta.1]: https://github.com/VladUZH/qwen-scribe/compare/v0.2.1-beta.1...v0.2.2-beta.1
[0.2.1-beta.1]: https://github.com/VladUZH/qwen-scribe/compare/v0.2.0-beta.1...v0.2.1-beta.1
[0.2.0-beta.1]: https://github.com/VladUZH/qwen-scribe/compare/v0.1.0-beta.1...v0.2.0-beta.1
[0.1.0-beta.1]: https://github.com/VladUZH/qwen-scribe/releases/tag/v0.1.0-beta.1
