"""PDF loader built on pdfplumber (text + tables).

One extraction-layer defect is repaired here because it can only be fixed before layout
analysis: some producers draw a page's text more than once (e.g. body, then running
header + body again, sometimes with a drifting vertical offset). pdfplumber then
interleaves glyphs of the two layers into garbled lines, and layout analysis becomes
very slow. Characters arrive in content-stream order, so each layer is a contiguous
*run* that starts near the top of the page; :func:`drop_duplicate_runs` keeps the first
run and discards later runs whose tokens are already covered by what was kept.

Glyph-level doubling that survives this (``"RReevveennuuee"``) is handled generically by
:mod:`core.ingestion.cleaner`.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pdfplumber

from core.models import PageText

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9,.'%-]{2,}")


def split_runs(
    chars: list[dict[str, Any]], page_height: float, restart_fraction: float = 0.3
) -> list[list[dict[str, Any]]]:
    """Split characters (in stream order) into runs at upward jumps ≥ ``restart_fraction`` of the page."""
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    threshold = restart_fraction * page_height
    for index, char in enumerate(chars):
        if current and chars[index - 1]["top"] - char["top"] > threshold:
            runs.append(current)
            current = []
        current.append(char)
    if current:
        runs.append(current)
    return runs


def drop_duplicate_runs(
    page: pdfplumber.page.Page,
    *,
    min_tokens: int = 20,
    coverage_threshold: float = 0.8,
    restart_fraction: float = 0.3,
) -> tuple[pdfplumber.page.Page, int]:
    """Return ``(page, dropped_runs)`` with duplicate text runs filtered out.

    A run is a duplicate when it has at least ``min_tokens`` tokens and at least
    ``coverage_threshold`` of them (with multiplicity) already occur in the runs kept so
    far. Multi-column layouts also produce several runs per page, but their tokens differ,
    so they are kept.
    """
    chars = page.chars
    runs = split_runs(chars, page.height, restart_fraction)
    if len(runs) < 2:
        return page, 0
    accepted: Counter[str] = Counter()
    keep: set[int] = set()
    dropped = 0
    for run in runs:
        tokens = [t.lower() for t in _TOKEN.findall("".join(c["text"] for c in run))]
        if len(tokens) >= min_tokens and accepted:
            pool = accepted.copy()
            covered = 0
            for token in tokens:
                if pool[token] > 0:
                    pool[token] -= 1
                    covered += 1
            if covered / len(tokens) >= coverage_threshold:
                dropped += 1
                continue
        accepted.update(tokens)
        keep.update(id(c) for c in run)
    if not dropped:
        return page, 0
    filtered = page.filter(lambda obj: obj.get("object_type") != "char" or id(obj) in keep)
    return filtered, dropped


class PdfLoader:
    """Load a PDF into pages with text and tables."""

    extensions = (".pdf",)

    def __init__(
        self,
        *,
        dedupe_tolerance: float = 1.0,
        extract_tables: bool = True,
        drop_duplicate_layers: bool = True,
    ) -> None:
        self.dedupe_tolerance = dedupe_tolerance
        self.extract_tables = extract_tables
        self.drop_duplicate_layers = drop_duplicate_layers

    def load(self, path: Path) -> list[PageText]:
        """Read every page; tables are returned as lists of rows of cell strings."""
        pages: list[PageText] = []
        dropped_runs = 0
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                working = page
                if self.drop_duplicate_layers:
                    working, dropped = drop_duplicate_runs(working)
                    dropped_runs += dropped
                working = working.dedupe_chars(tolerance=self.dedupe_tolerance)
                text = working.extract_text() or ""
                tables = self._tables(working) if self.extract_tables else []
                pages.append(PageText(page=index, text=text, tables=tables))
        if dropped_runs:
            log.info("%s: dropped %d duplicate text run(s)", path.name, dropped_runs)
        return pages

    @staticmethod
    def _tables(page: pdfplumber.page.Page) -> list[list[list[str]]]:
        try:
            raw = page.extract_tables() or []
        except Exception as exc:  # pragma: no cover - pdfplumber edge cases
            log.warning("table extraction failed on page %s: %s", page.page_number, exc)
            return []
        tables: list[list[list[str]]] = []
        for table in raw:
            rows = [[(cell or "").strip() for cell in row] for row in table if row]
            rows = [row for row in rows if any(cell for cell in row)]
            if rows:
                tables.append(rows)
        return tables
