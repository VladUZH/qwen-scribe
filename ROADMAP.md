# Roadmap

The roadmap favors a trustworthy small release before adding many features.

## v0.1 beta — publication ready

- [x] Source-only repository with no weights, environments, or generated apps
- [x] Reproducible Mac app build and beta zip
- [x] Local privacy and request-security documentation
- [x] Model-free API tests and CI
- [ ] Fresh-clone test on a second Apple Silicon Mac
- [x] Enable GitHub private vulnerability reporting
- [x] Add one polished application screenshot and a short dictation demo

## v0.2 — dependable distribution

- [ ] Developer ID signing and Apple notarization — the release workflow signs
      automatically once the owner's Developer ID secrets are configured, and
      `scripts/notarize.sh` staples; blocked only on the credentials
- [x] Automated release workflow with checksums and provenance notes
- [x] In-app configurable dictation hotkey, model, and language
- [x] Menu-bar status and explicit quit/restart controls
- [x] Search and export across saved transcripts

## v0.3 — broader workflow support

- Fn as a push-to-talk key — needs IOHIDManager tracking of the physical key,
  because macOS synthesizes fn-flagged events around every arrow/navigation
  key and a naive listener would start dictation on PageUp
- Optional launch at login
- Editable transcript titles and text
- Batch import and export
- Accessibility review, keyboard-only UI audit, and localization foundation

## Later, only with evidence

Speaker diarization, additional formats, streaming partial dictation, and plugin
integrations are candidates. They should not weaken the local-only privacy model
or complicate the core push-to-talk experience without clear user value.
