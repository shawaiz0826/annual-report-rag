"""Plain-text / Markdown loader. Form-feed characters split pages."""

from __future__ import annotations

from pathlib import Path

from core.models import PageText


class TextLoader:
    r"""Load .txt / .md files; ``\f`` (form feed) separates pages."""

    extensions = (".txt", ".md")

    def load(self, path: Path) -> list[PageText]:
        """Read the file as UTF-8 (errors replaced) and split on form feeds."""
        raw = path.read_text(encoding="utf-8", errors="replace")
        parts = [p for p in raw.split("\f")] or [""]
        return [PageText(page=i, text=part) for i, part in enumerate(parts, start=1)]
