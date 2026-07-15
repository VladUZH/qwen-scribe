"""Fast publication checks that do not download models or dependencies."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_TRACKED_BYTES = 10 * 1024 * 1024
REQUIRED = {
    ".gitignore",
    "LICENSE",
    "NOTICE",
    "PRIVACY.md",
    "README.md",
    "requirements-lock.txt",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
}
FORBIDDEN_PARTS = {
    ".DS_Store",
    ".venv",
    "__pycache__",
    "dist",
    "models",
    "_CodeSignature",
}
FORBIDDEN_SUFFIXES = {".safetensors", ".bin", ".pyc"}


def source_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    return [ROOT / item.decode() for item in output.split(b"\0") if item]


def main() -> int:
    errors: list[str] = []
    files = source_files()
    relative = {str(path.relative_to(ROOT)) for path in files}

    for required in sorted(REQUIRED - relative):
        errors.append(f"missing required publication file: {required}")

    for path in files:
        rel = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS or part.endswith(".app") for part in rel.parts):
            errors.append(f"generated/private path would be published: {rel}")
        if path.suffix in FORBIDDEN_SUFFIXES:
            errors.append(f"binary/model artifact would be published: {rel}")
        if path.is_file() and path.stat().st_size > MAX_TRACKED_BYTES:
            errors.append(f"file exceeds 10 MiB publication limit: {rel}")

    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    if "fonts.googleapis.com" in html or "fonts.gstatic.com" in html:
        errors.append("web UI still makes an external font request")

    for script in (
        ROOT / "run.sh",
        ROOT / "scripts" / "setup_python.sh",
        ROOT / "scripts" / "build_macos_apps.sh",
        ROOT / "scripts" / "package_release.sh",
    ):
        if not os.access(script, os.X_OK):
            errors.append(f"script is not executable: {script.relative_to(ROOT)}")

    if errors:
        print("Repository checks failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Repository checks passed ({len(files)} source files, no large/model artifacts).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
