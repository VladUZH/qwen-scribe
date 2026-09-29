"""Native behavior checks: no permissions, clipboard writes, or model needed."""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "requires macOS and clang")
class NativeDictationTests(unittest.TestCase):
    def test_paste_last_dictation(self):
        script = (ROOT / "scripts/build_macos_apps.sh").read_text()
        frameworks = re.findall(r"-framework (\w+)", script)
        with tempfile.TemporaryDirectory(prefix="qwen-scribe-native-test-") as directory:
            binary = Path(directory) / "paste-last"
            command = ["clang", "-fobjc-arc", "-mmacosx-version-min=14.0", "-Wall", "-Wextra",
                       "-Wno-unused-parameter"]
            for framework in frameworks:
                command.extend(["-framework", framework])
            command.extend([str(ROOT / "tests/native/paste_last.m"), "-o", str(binary)])
            build = subprocess.run(command, capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("native checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
