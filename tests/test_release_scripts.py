"""Failure-path checks for destructive release operations."""

import os
import re
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def write_executable(path: Path, contents: str) -> None:
    """A stand-in command on PATH, so a destructive script can be run for real."""
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


class NotarizationScriptTests(unittest.TestCase):
    def test_failed_assessment_preserves_the_submitted_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "Qwen-Scribe-0.2.0-macos-arm64.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(
                    "Qwen-Scribe-0.2.0/Qwen Scribe.app/Contents/marker",
                    "main",
                )
                bundle.writestr(
                    "Qwen-Scribe-0.2.0/Stop Qwen Scribe.app/Contents/marker",
                    "stop",
                )
            original = archive.read_bytes()

            commands = root / "commands"
            commands.mkdir()
            write_executable(commands / "xcrun", "#!/bin/sh\nexit 0\n")
            write_executable(commands / "spctl", "#!/bin/sh\nexit 1\n")
            write_executable(
                commands / "ditto",
                """#!/usr/bin/env python3
import sys
import zipfile

if sys.argv[1:3] != ["-x", "-k"]:
    raise SystemExit("unexpected repack before assessment")
with zipfile.ZipFile(sys.argv[3]) as archive:
    archive.extractall(sys.argv[4])
""",
            )

            environment = os.environ.copy()
            environment["PATH"] = f"{commands}{os.pathsep}{environment['PATH']}"
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "notarize.sh"), str(archive)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Verifying the stapled apps", result.stdout)
            self.assertTrue(archive.is_file())
            self.assertEqual(archive.read_bytes(), original)


class BundledRuntimePinTests(unittest.TestCase):
    """The runtime pin is what someone else's Mac will run, so it is checked
    like a dependency: named exactly, hashed, and internally consistent."""

    def setUp(self):
        self.script = (ROOT / "scripts" / "bundle_python.sh").read_text()
        self.pins = dict(
            line.split("=", 1) for line in self.script.splitlines()
            if line.startswith("PYTHON_") and "=" in line
        )
        self.pins = {key: value.strip('"') for key, value in self.pins.items()}

    def test_the_archive_url_and_name_agree_with_the_version(self):
        version, release = self.pins["PYTHON_VERSION"], self.pins["PYTHON_RELEASE"]
        self.assertRegex(version, r"^3\.12\.\d+$")     # the floor the lock needs
        self.assertRegex(release, r"^\d{8}$")
        archive = self.pins["PYTHON_ARCHIVE"]
        self.assertIn("${PYTHON_VERSION}+${PYTHON_RELEASE}", archive)
        self.assertIn("aarch64-apple-darwin", archive)
        self.assertIn("install_only", archive)
        self.assertIn("${PYTHON_RELEASE}", self.pins["PYTHON_URL"])
        self.assertIn("${PYTHON_ARCHIVE}", self.pins["PYTHON_URL"])

    def test_the_hash_is_a_sha256(self):
        self.assertRegex(self.pins["PYTHON_SHA256"], r"^[0-9a-f]{64}$")

    def test_a_mismatching_archive_is_refused_rather_than_bundled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            cache.mkdir()
            # An archive already in the cache under the expected name, with
            # the wrong contents: the build must stop, not unpack it.
            (cache / self.pins["PYTHON_ARCHIVE"].replace(
                "${PYTHON_VERSION}", self.pins["PYTHON_VERSION"]).replace(
                "${PYTHON_RELEASE}", self.pins["PYTHON_RELEASE"])).write_bytes(b"not a runtime")
            commands = root / "commands"
            commands.mkdir()
            # No network: a download attempt fetches the same wrong bytes.
            write_executable(commands / "curl", "#!/bin/sh\nprintf 'not a runtime' > \"$4\"\n")
            environment = dict(
                os.environ,
                PATH=f"{commands}:{os.environ['PATH']}",
                QS_BUILD_CACHE=str(cache),
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "bundle_python.sh"), str(root / "out")],
                capture_output=True, text=True, env=environment,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("pinned SHA-256", result.stderr)
            self.assertFalse((root / "out").exists())


class HelperLinkTests(unittest.TestCase):
    """A missing framework is a link error no other suite can reach: the
    helper compiles only on a Mac, so CI finds it minutes into a build.
    Each entry names functions that one framework alone provides. A framework
    the helper uses only for headers is absent on purpose: AudioToolbox gives
    it AudioChannelLayout and kAudioFormatLinearPCM, which are a struct and an
    enum, so nothing of it has to be linked."""

    FRAMEWORKS = {
        "AVFoundation": r"\bAV(?:URLAsset|AssetReader|AudioRecorder|CaptureDevice)\b",
        "ApplicationServices": r"\bCGEvent(?:Post|Create\w+|SourceCreate)\b",
        "Cocoa": r"\bNS(?:Application|StatusItem|Window)\b",
        "CoreMedia": r"\bCM(?:SampleBuffer|BlockBuffer|Time)\w*\b",
        "IOKit": r"\bIOHID\w+\b",
        "ServiceManagement": r"\bSMAppService\b",
    }

    def setUp(self):
        self.source = (ROOT / "native" / "DictationHelper.m").read_text()
        script = (ROOT / "scripts" / "build_macos_apps.sh").read_text()
        # The link line is one continued command; collapse it to search it.
        start = script.index("\nclang ") + 1
        end = script.index("\n\n", start)
        self.link_line = " ".join(script[start:end].split())

    def test_every_framework_the_helper_uses_is_linked(self):
        for framework, identifiers in sorted(self.FRAMEWORKS.items()):
            used = re.search(identifiers, self.source)
            if not used:
                continue
            self.assertIn(
                f"-framework {framework}", self.link_line,
                f"{used.group(0)} needs -framework {framework}",
            )

    def test_the_table_still_describes_this_helper(self):
        """A table nothing matches would pass the check above vacuously."""
        for framework, identifiers in sorted(self.FRAMEWORKS.items()):
            self.assertRegex(self.source, identifiers, f"{framework} entry matches nothing")


if __name__ == "__main__":
    unittest.main()


