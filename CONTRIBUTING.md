# Contributing

Thanks for helping make private local dictation easier to use. Qwen Scribe is an
early beta, so small, well-tested changes are especially valuable.

## Before opening a change

- Use an issue for a substantial feature or architectural change so the scope
  can be agreed before implementation.
- Use the Security tab rather than an issue for vulnerabilities described in
  [SECURITY.md](SECURITY.md).
- Keep the application local-first. New network services, analytics, accounts,
  or background data transmission require explicit maintainer approval and a
  privacy-document update.
- Do not add model weights, media samples without redistribution rights,
  virtual environments, generated app bundles, credentials, or user data.

## Development setup

An Apple Silicon Mac with macOS 14+, Python 3.12+, and Apple Command Line Tools
is required for full development.

```bash
make setup       # runtime environment from requirements-lock.txt
make setup-test  # the lighter dependency set CI uses
make check
make app
```

The Python/API test suite deliberately imports MLX lazily. On non-Mac systems,
CI installs `requirements-test.txt` and can test history, upload validation,
and request security without downloading a model. Use `make setup-test` to
reproduce that environment locally when a test passes for you but fails in CI.

## Pull requests

1. Keep each pull request focused on one coherent change.
2. Add or update tests for behavior changes.
3. Update README, privacy, security, third-party notices, and changelog when the
   change affects those contracts.
4. Run `make check` and, for native or packaging changes, `make app`.
5. Describe manual validation, macOS version, Mac model, and whether dictation
   permissions were freshly granted or already present.

Contributions are accepted under the repository's Apache-2.0 license. Under
section 5 of that license, intentionally submitted contributions use the same
terms unless explicitly stated otherwise.

## Code map

- `server.py`: the entry point that runs the application
- `qwen_scribe/`: the application — `config` (paths, limits, tables),
  `settings` (persisted settings), `sessions` (model sessions), `jobs` (job
  store, worker, cancel/retry/shutdown), `history` (transcripts, search,
  export), `dictation` (helper heartbeat), `api` (routes and middleware)
- `static/index.html`: dependency-free browser interface
- `static/transcript.js`: the sentence splitter and SRT builder the page
  loads, tested under Node in `tests/js/`
- `native/DictationHelper.m`: global shortcut, recording, HUD, and paste bridge
- `macos/`: tracked app launchers and property-list sources
- `scripts/`: environment, checks, app build, and release packaging
- `tests/`: model-free API and storage tests, plus the Node tests in `tests/js/`

Prefer standard-library code for repository tooling. Keep the browser interface
free of remote assets and build-time JavaScript dependencies unless there is a
clear, reviewed benefit.
