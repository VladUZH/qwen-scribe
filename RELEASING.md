# Releasing Qwen Scribe

Releases are built by CI from a tag and land as **drafts**; a human reviews and
publishes them. Nothing becomes public from automation alone.

## Cutting a release

1. Make sure `main` is green and `CHANGELOG.md` has a dated section for the
   version. The bundle version lives in three places that must agree:
   `macos/QwenScribe-Info.plist`, the `VERSION` default in
   `scripts/build_macos_apps.sh`, and `version` in `pyproject.toml`. After
   changing the last one run `uv lock`, which records the project version in
   `uv.lock`; the lock check in `make check` and CI fails until it matches.
2. Cut the tag, in either of two ways:

   ```bash
   git tag v0.2.1-beta.1
   git push origin v0.2.1-beta.1
   ```

   or, from GitHub alone, land a commit on `main` whose subject is
   `Release v0.2.1-beta.1` (squash-merge a pull request with that title, or
   push such a commit), or open Actions → **Tag release** → Run workflow and
   enter the version. The `Tag release` workflow checks that the version
   matches the bundle and `pyproject.toml`, refuses an existing tag, pushes
   the tag, and starts the `Release` workflow on it.

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

## The bundled Python runtime

`make app` puts a Python 3.12 runtime inside the bundle at
`Contents/Resources/Python`, so the app needs no Python on the Mac it runs
on. `scripts/bundle_python.sh` holds the pin: a `python-build-standalone`
`install_only` build for `aarch64-apple-darwin`, named by version and
verified against a SHA-256 that is committed here. A mismatch stops the
build. The archive is cached under `.build/cache`, and Tcl/Tk is removed
from it; nothing else is. Expect the app to grow by roughly 25 MB
compressed.

It sits in `Resources` rather than `Frameworks` because codesign treats
everything under `Frameworks` as nested code and wants a signature for every
file it thinks is executable — which includes the shebang lines the stdlib
carries on `pdb.py`, `tarfile.py` and two dozen others. Under `Resources`
they are sealed by hash into the app's own signature instead. If notarization
later insists on a framework layout, the runtime has to be repackaged as a
real `Python.framework`; the release dry run is where that will show up.

Every Mach-O file inside that runtime is signed before the bundle is, and
`codesign --verify --deep --strict` runs on the result: macOS reports a
bundle with unsigned nested code as *damaged*, which the user sees instead
of an app. `BUNDLE_PYTHON=0 make app` builds without it, for a local edit or
an offline machine, and the launcher then looks for a system Python as it
used to.

**When the Developer ID arrives**, the interpreter is signed with
`macos/QwenScribePython.entitlements`, which carries
`com.apple.security.cs.disable-library-validation`. The private environment
holds wheels from PyPI whose libraries Apple did not sign with this Team ID,
and under the hardened runtime library validation refuses to load them: the
app would start and then fail on its first import. The build script already
applies that entitlement to the interpreter and only to the interpreter.
Notarizing a bundle with a nested runtime is not yet proven — the release
dry run is the place to find out, before a tag depends on it.

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
