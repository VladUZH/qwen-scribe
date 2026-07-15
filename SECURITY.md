# Security policy

## Supported versions

Until the first stable release, security fixes are made on the latest `main`
branch and the newest `0.1.x` beta release only.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose local files,
record audio unexpectedly, inject text, or execute code. Use **Report a
vulnerability** in the
[repository's Security tab](https://github.com/VladUZH/qwen-scribe/security/advisories/new).
The repository owner must enable GitHub private vulnerability reporting before
the first public release.

Include the affected version, macOS version, reproduction steps, impact, and
any suggested mitigation. Maintainers should acknowledge a complete report
within seven days and coordinate disclosure after a fix is available.

## Security boundaries

- Qwen Scribe is a single-user localhost application, not a network service.
- The server must remain bound to `127.0.0.1`; LAN or internet exposure is not
  supported and there is no authentication layer for it.
- The macOS Accessibility, Input Monitoring, and Microphone grants are powerful.
  Only install builds produced from source you trust or official signed release
  artifacts.
- Transcript history is local but unencrypted. It should not be treated as a
  secrets vault.
- Model files and Python packages are supply-chain dependencies. Release builds
  pin direct Python dependencies, exclude model weights, and obtain weights from
  the upstream model repository.

Automated dependency updates and repository checks are configured, but they do
not replace review of dependency release notes or code-signing provenance.
