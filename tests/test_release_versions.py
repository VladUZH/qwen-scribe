"""Release-version validation shared by local scripts and GitHub Actions."""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERSION_LIBRARY = ROOT / "scripts" / "release_versions.sh"


class ReleaseVersionTests(unittest.TestCase):
    def accepted(self, function: str, value: str) -> bool:
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'source "$1"; {function} "$2"',
                "qwen-scribe-version-test",
                str(VERSION_LIBRARY),
                value,
            ],
            cwd=ROOT,
            check=False,
        )
        return result.returncode == 0

    def test_bundle_versions_require_three_numeric_components(self):
        for valid in ("0.2.0", "10.20.300"):
            self.assertTrue(self.accepted("qs_valid_bundle_version", valid), valid)
        for invalid in ("0", "0.2", "0.2.0.1", "v0.2.0", "0.2.0-beta.1"):
            self.assertFalse(self.accepted("qs_valid_bundle_version", invalid), invalid)

    def test_release_versions_allow_only_safe_semver_prereleases(self):
        for valid in ("0.2.0", "0.2.0-beta.1", "10.20.300-rc.2"):
            self.assertTrue(self.accepted("qs_valid_release_version", valid), valid)
        for invalid in (
            "0",
            "0.2",
            "v0.2.0",
            "0.2.0-",
            "0.2.0-../../../escape",
            "0.2.0 beta",
        ):
            self.assertFalse(self.accepted("qs_valid_release_version", invalid), invalid)


if __name__ == "__main__":
    unittest.main()
