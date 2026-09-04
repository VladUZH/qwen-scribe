"""Deterministic tidying of dictated text before it is pasted.

Three steps, each plain string work with no model behind it, applied to
dictation alone: spoken layout commands, the user's own replacements, and
whitespace normalisation. File transcripts are never touched.
"""

from __future__ import annotations

import re

# Spoken layout commands, by the language the model reported. A command is
# honoured only when it stands on its own: right after sentence punctuation
# or at the start, and followed by punctuation or the end. "a new line of
# products" is prose and stays prose; "…done. New paragraph. Next…" is an
# instruction. Qwen3-ASR's punctuation is what makes the boundary reliable.
COMMANDS = {
    "english": [
        (r"new\s+paragraph", "\n\n"),
        (r"new\s+line", "\n"),
    ],
}

_COMMAND_PATTERNS: dict[str, list[tuple[re.Pattern, str]]] = {
    language: [
        (
            re.compile(
                r"(?:^|(?<=[.!?,;:\n]))\s*" + phrase + r"\s*(?:[.!?,;:]+|(?=\n)|$)",
                re.IGNORECASE | re.MULTILINE,
            ),
            replacement,
        )
        for phrase, replacement in table
    ]
    for language, table in COMMANDS.items()
}

MAX_REPLACEMENTS = 100
MAX_REPLACEMENT_CHARS = 100


def valid_replacements(value: object) -> bool:
    """The settings validator for ``dictation.replacements``."""
    if not isinstance(value, list) or len(value) > MAX_REPLACEMENTS:
        return False
    for item in value:
        if not isinstance(item, dict) or set(item) != {"from", "to"}:
            return False
        source, target = item["from"], item["to"]
        if not isinstance(source, str) or not isinstance(target, str):
            return False
        if not source.strip() or len(source) > MAX_REPLACEMENT_CHARS:
            return False
        if len(target) > MAX_REPLACEMENT_CHARS:
            return False
    return True


def apply_commands(text: str, language: str | None) -> str:
    patterns = _COMMAND_PATTERNS.get((language or "").strip().lower(), [])
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def apply_replacements(text: str, replacements: list[dict]) -> str:
    """Whole-word, case-insensitive, longest phrase first.

    Longest first so "Qwen Scribe" wins over a separate "Qwen" entry, and
    boundaries so "cat" never rewrites "catalogue".
    """
    for item in sorted(replacements, key=lambda item: -len(item["from"].strip())):
        source = item["from"].strip()
        if not source:
            continue
        pattern = re.compile(
            r"(?<![\w'])" + re.escape(source).replace(r"\ ", r"\s+") + r"(?![\w'])",
            re.IGNORECASE,
        )
        text = pattern.sub(lambda _match, target=item["to"]: target, text)
    return text


def normalise(text: str) -> str:
    """Whitespace only: runs of spaces to one, no trailing spaces, at most
    one blank line, no leading or trailing whitespace."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # A command that landed right before punctuation leaves "\n." behind.
    text = re.sub(r"\n+([.!?,;:])", r"\1", text)
    return text.strip()


def dictation_text(text: str, language: str | None, dictation_settings: dict) -> str:
    """Everything applied to a finished dictation, in order."""
    if dictation_settings.get("spoken_commands", True):
        text = apply_commands(text, language)
    text = apply_replacements(text, dictation_settings.get("replacements") or [])
    return normalise(text)
