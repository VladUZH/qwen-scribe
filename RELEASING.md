# Releasing Qwen Scribe

Releases are built by CI from a tag and land as **drafts**; a human reviews and
publishes them. Nothing becomes public from automation alone.

## Cutting a release

1. Make sure `main` is green and `CHANGELOG.md` has a dated section for the
   version.
2. Tag and push:

   ```bash
   git tag v0.2.1-beta.1
   git push origin v0.2.1-beta.1
   ```

3. The `Release` workflow builds the apps on a GitHub `macos-15` runner, runs
   the tests and repository checks, packages the archive, and creates a
   **draft** release containing:
   - `Qwen-Scribe-<version>-macos-arm64.zip`
   - `SHA256SUMS.txt`
   - provenance notes: source commit, workflow run, builder, compiler,
     dependency-lock hash, and signing mode
4. Download the draft's archive, unpack it, and try it on a real Mac. For a
   Developer ID-signed build, specifically verify that dictation records: the
   hardened runtime silently blocks the microphone if the audio-input
   entitlement was lost, and only a hands-on test catches that.
5. Optionally sign and notarize (below), re-upload, and update the checksum.
6. Publish the draft.

Tag → version rules: the tag `v0.2.1-beta.1` names the archive; the app
bundles inside carry `CFBundleShortVersionString` `0.2.1` (Apple's field does
not accept pre-release suffixes) and `CFBundleVersion` = the workflow run
number. `scripts/package_release.sh` refuses a tag that does not match the
bundle's version.

## Developer ID signing (optional today, required for 1.0)

CI signs with the Developer ID automatically when these repository secrets
exist; without them it produces an ad-hoc-signed build and says so in the
release notes:

| Secret | Content |
| --- | --- |
| `MACOS_CERTIFICATE_P12` | Developer ID Application certificate, p12, base64 |
| `MACOS_CERTIFICATE_PASSWORD` | password for the p12 |
| `MACOS_CODESIGN_IDENTITY` | e.g. `Developer ID Application: Name (TEAMID)` |

## Notarization

Two supported paths. Both produce a draft whose `SHA256SUMS.txt` matches the
archive users download — the ordering guarantee that matters.

### In CI (recommended once credentials exist)

Add three more repository secrets and the workflow notarizes and staples
*before* packaging and checksumming, so the draft is correct by construction:

| Secret | Content |
| --- | --- |
| `APPLE_API_KEY_ID` | App Store Connect API key ID |
| `APPLE_API_ISSUER_ID` | App Store Connect issuer ID |
| `APPLE_API_KEY_P8` | the `.p8` key file, base64 |

Create the key under App Store Connect → Users and Access → Integrations,
with the Developer role. If keeping notarization credentials out of GitHub is
preferred, skip these secrets and use the local path.

### Locally (fallback / escape hatch)

After the draft exists and the build is Developer ID signed:

```bash
xcrun notarytool store-credentials qwen-scribe \
  --apple-id <apple-id> --team-id <team-id> --password <app-specific-password>
scripts/notarize.sh dist/Qwen-Scribe-<version>-macos-arm64.zip
```

The script staples, re-packs, and **regenerates `SHA256SUMS.txt` itself** —
re-packing changes the hash, and a stale published checksum makes a legitimate
build look tampered with. Re-upload *both* files together:

```bash
gh release upload v<version> --clobber dist/Qwen-Scribe-*.zip dist/SHA256SUMS.txt
```

## Verifying a download (for users)

```bash
shasum -a 256 -c SHA256SUMS.txt
```
