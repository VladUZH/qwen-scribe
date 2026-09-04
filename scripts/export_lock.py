"""Export uv.lock to requirements-lock.txt, or check that everything agrees.

pyproject.toml is the dependency source of truth; `uv lock` resolves it into
uv.lock; this script exports that into requirements-lock.txt, the flat file the
launcher, ./run.sh, and release CI install with plain pip. Run without
arguments to regenerate, or with --check to fail when any of these disagree:

- requirements-lock.txt versus the export of uv.lock
- uv.lock versus pyproject.toml (uv's own --locked check)
- requirements.txt versus the direct dependencies in pyproject.toml
- requirements-test.txt versus the versions in requirements-lock.txt, for the
  packages they share, so a test never passes on a version users do not get

Standard library only, apart from the uv binary it shells out to.
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_EXPORT = ROOT / "requirements-lock.txt"
DIRECT = ROOT / "requirements.txt"
TEST = ROOT / "requirements-test.txt"
PYPROJECT = ROOT / "pyproject.toml"

# pyproject.toml scopes the resolution to Apple Silicon Macs, so uv stamps
# this marker on every exported line. The file is documented as macOS arm64
# only; the marker adds nothing but noise, so it is stripped on export.
MARKER = " ; platform_machine == 'arm64' and sys_platform == 'darwin'"

HEADER = """\
# Fully resolved runtime for macOS arm64 on Python 3.12 or newer. Generated
# from uv.lock by `make lock` (scripts/export_lock.py); do not edit by hand.
# To change a dependency: edit pyproject.toml, run `uv lock`, then `make lock`.
# CI fails when this file has drifted from uv.lock. Release CI validates it on
# macOS arm64 with Python 3.12.
"""

# Packages the test set needs that users never get. Kept out of the
# lock-agreement check on purpose.
TEST_ONLY = {"httpx2"}


def _pins(lines) -> dict[str, str]:
    """{normalised name: version} for every `name==version` line."""
    pins = {}
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith(MARKER):
            line = line[: -len(MARKER)]
        match = re.fullmatch(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==(\S+)", line)
        if not match:
            raise SystemExit(f"cannot parse pin: {line!r}")
        pins[_normalise(match.group(1))] = match.group(3)
    return pins


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def exported_pins() -> dict[str, str]:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required: pip install uv, or brew install uv")
    result = subprocess.run(
        [uv, "export", "--format", "requirements-txt", "--locked", "--no-hashes",
         "--no-emit-project", "--no-annotate", "--no-header"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        # --locked makes uv refuse when uv.lock no longer matches pyproject.toml.
        raise SystemExit(f"uv export failed:\n{result.stderr.strip()}")
    return _pins(result.stdout.splitlines())


def render(pins: dict[str, str]) -> str:
    body = "\n".join(f"{name}=={version}" for name, version in sorted(pins.items()))
    return HEADER + body + "\n"


def check() -> int:
    if shutil.which("uv") is None:
        # A contributor without uv can still run `make check`; CI installs uv
        # and so always enforces the lock agreement.
        print("uv is not installed; skipping the lock check (CI enforces it).")
        return 0
    errors: list[str] = []
    wanted = exported_pins()
    current = _pins(LOCK_EXPORT.read_text(encoding="utf-8").splitlines())
    if current != wanted:
        diff = difflib.unified_diff(
            render(current).splitlines(), render(wanted).splitlines(),
            "requirements-lock.txt", "export of uv.lock", lineterm="",
        )
        errors.append("requirements-lock.txt has drifted from uv.lock; run `make lock`:\n  "
                      + "\n  ".join(list(diff)[2:]))

    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = _pins(project["project"]["dependencies"])
    direct = _pins(DIRECT.read_text(encoding="utf-8").splitlines())
    if declared != direct:
        errors.append(f"requirements.txt {direct} does not match pyproject.toml {declared}")

    test = _pins(TEST.read_text(encoding="utf-8").splitlines())
    for name, version in sorted(test.items()):
        if name in TEST_ONLY:
            continue
        if name not in wanted:
            errors.append(f"requirements-test.txt pins {name}, which is not in the lock")
        elif wanted[name] != version:
            errors.append(f"requirements-test.txt pins {name}=={version} but the lock has {wanted[name]}")

    if errors:
        print("Lock check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Lock check passed ({len(wanted)} pinned packages agree everywhere).")
    return 0


def regenerate() -> int:
    pins = exported_pins()
    LOCK_EXPORT.write_text(render(pins), encoding="utf-8")
    # Keep the test set on the same versions users get.
    lines = TEST.read_text(encoding="utf-8").splitlines()
    updated = []
    for line in lines:
        match = re.fullmatch(r"([A-Za-z0-9_.\-]+)==(\S+)", line.strip())
        if match and _normalise(match.group(1)) in pins:
            updated.append(f"{match.group(1)}=={pins[_normalise(match.group(1))]}")
        else:
            updated.append(line)
    TEST.write_text("\n".join(updated) + "\n", encoding="utf-8")
    print(f"Wrote {LOCK_EXPORT.name} ({len(pins)} packages) and aligned {TEST.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(check() if "--check" in sys.argv[1:] else regenerate())
