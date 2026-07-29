# Changelog

All notable changes will be documented here. The project follows Semantic
Versioning after `1.0.0`; beta versions may still change setup and storage
details when clearly documented.

## [Unreleased]

Nothing yet.

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

- Made the native recorder the app bundle's declared main executable so macOS
  Accessibility, Input Monitoring, and Microphone permissions persist across
  normal stop/start cycles

### Changed

- Added reviewed direct dependencies and a fully resolved runtime lock
- Removed remote web-font requests in favor of macOS system fonts
- Raised the documented and bundled minimum to macOS 14 for current MLX
- Mac app builds now create their own private environment rather than copying a
  developer `.venv`
- Model weights and generated app bundles are explicitly excluded from source

[Unreleased]: https://github.com/VladUZH/qwen-scribe/compare/v0.1.0-beta.1...HEAD
[0.1.0-beta.1]: https://github.com/VladUZH/qwen-scribe/releases/tag/v0.1.0-beta.1
