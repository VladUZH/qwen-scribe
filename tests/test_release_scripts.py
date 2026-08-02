"""Failure-path checks for destructive release operations."""

import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


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
            self.write_executable(commands / "xcrun", "#!/bin/sh\nexit 0\n")
            self.write_executable(commands / "spctl", "#!/bin/sh\nexit 1\n")
            self.write_executable(
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

    @staticmethod
    def write_executable(path: Path, contents: str) -> None:
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
