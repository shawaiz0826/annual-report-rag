"""Versioned prompt files. Each ``*.md`` starts with a ``version:`` header line."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
_VERSION = re.compile(r"^\s*version:\s*(\S+)\s*$", re.MULTILINE)


@cache
def load_prompt(name: str) -> tuple[str, str]:
    """Return ``(version, body)`` for ``prompts/<name>.md``.

    The header block is everything before the first blank line; it must contain a
    ``version: <id>`` line, which is recorded in every trace.
    """
    path = PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    match = _VERSION.search(text)
    if not match:
        raise ValueError(f"{path.name} has no 'version:' header")
    header, _, body = text.partition("\n\n")
    return match.group(1), body.strip()


def prompt_versions() -> dict[str, str]:
    """Version of every prompt file, for traces."""
    return {p.stem: load_prompt(p.stem)[0] for p in sorted(PROMPTS_DIR.glob("*.md"))}
