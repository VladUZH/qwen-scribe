# Changelog

All notable changes will be documented here. The project follows Semantic
Versioning after `1.0.0`; beta versions may still change setup and storage
details when clearly documented.

## [Unreleased]

### Added

- Reproducible source, app-bundle, and beta-archive build commands
- Repository publication checks and GitHub Actions CI
- Privacy, security, contribution, conduct, license, and third-party notices
- Local Host-header, cross-origin request, and browser security-header protection

### Changed

- Added reviewed direct dependencies and a fully resolved runtime lock
- Removed remote web-font requests in favor of macOS system fonts
- Raised the documented and bundled minimum to macOS 14 for current MLX
- Mac app builds now create their own private environment rather than copying a
  developer `.venv`
- Model weights and generated app bundles are explicitly excluded from source

## [0.1.0-beta.1] - Unreleased

- Local audio/video transcription with Qwen3-ASR on MLX
- Automatic transcript history and deletion controls
- System-wide hold-right-Command dictation with clipboard restoration
- Floating listening, transcribing, completion, and error HUD
