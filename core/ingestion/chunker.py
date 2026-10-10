"""Chunking: page-bounded fixed windows plus table chunks, every chunk headed with provenance."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from core.models import Chunk, DocumentProfile, PageText

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class ChunkingStrategy(Protocol):
    """Turns a document's cleaned pages into chunks."""

    def chunk(self, pages: list[PageText], profile: DocumentProfile) -> list[Chunk]:
        """Return chunks for ``pages``; never crosses a page boundary."""
        ...


@dataclass(frozen=True)
class ChunkingOptions:
    """Tunables for :class:`FixedWindowChunker` (mirrors ``config.yaml: chunking``)."""

    chunk_chars: int = 800
    overlap_chars: int = 100
    table_chunks: bool = True
    min_table_rows: int = 2


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def split_windows(text: str, size: int, overlap: int) -> list[str]:
    """Split ``text`` into windows of ≈``size`` chars with ``overlap`` chars of context.

    Break points are chosen, in order of preference, at a paragraph break, a sentence
    end, a line break, or whitespace, searching backwards from the window end.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    overlap = max(0, min(overlap, size // 2))
    windows: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            end = _best_break(text, start, end)
        piece = text[start:end].strip()
        if piece:
            windows.append(piece)
        if end >= len(text):
            break
        next_start = max(end - overlap, start + 1)
        # never restart mid-word
        while next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start
    return windows


def _best_break(text: str, start: int, end: int) -> int:
    floor = start + (end - start) // 2
    window = text[start:end]
    for pattern in ("\n\n", None, "\n", " "):
        if pattern is None:
            matches = [m.end() for m in _SENTENCE_END.finditer(window)]
            idx = max((m for m in matches if start + m > floor), default=-1)
            if idx > 0:
                return start + idx
            continue
        idx = window.rfind(pattern)
        if idx >= 0 and start + idx > floor:
            return start + idx + len(pattern)
    return end


def table_to_markdown(rows: list[list[str]]) -> str:
    """Render a cell grid as a Markdown table (first row is the header)."""
    width = max(len(r) for r in rows)
    norm = [
        [(c or "").replace("\n", " ").replace("|", "/").strip() for c in r]
        + [""] * (width - len(r))
        for r in rows
    ]
    header, body = norm[0], norm[1:]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _nearest_heading(page_text: str, first_cell: str) -> str | None:
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    if not lines:
        return None
    cutoff = len(lines)
    if first_cell:
        for i, ln in enumerate(lines):
            if first_cell[:30] in ln:
                cutoff = i
                break
    for ln in reversed(lines[:cutoff]):
        if (
            3 <= len(ln) <= 90
            and not ln.endswith((".", ","))
            and not re.fullmatch(r"[\d\s.,()%-]+", ln)
        ):
            return ln
    return lines[0] if lines else None


class FixedWindowChunker:
    """Fixed character windows per page, plus one chunk per extracted table."""

    def __init__(self, options: ChunkingOptions | None = None) -> None:
        self.options = options or ChunkingOptions()

    def chunk(self, pages: list[PageText], profile: DocumentProfile) -> list[Chunk]:
        """Chunk every page; see module docstring."""
        chunks: list[Chunk] = []
        meta = profile.flat_metadata()
        for page in pages:
            header = profile.provenance_header(page.page)
            for n, window in enumerate(
                split_windows(page.text, self.options.chunk_chars, self.options.overlap_chars)
            ):
                text = f"{header}\n{window}"
                chunks.append(
                    Chunk(
                        chunk_id=f"{profile.document_id}:p{page.page}:c{n}",
                        document_id=profile.document_id,
                        page=page.page,
                        chunk_type="text",
                        text=text,
                        metadata={**meta, "page": page.page, "chunk_type": "text"},
                        content_hash=_content_hash(text),
                    )
                )
            if not self.options.table_chunks:
                continue
            t_index = 0
            for table in page.tables:
                if len(table) < self.options.min_table_rows:
                    continue
                first_cell = next((c for c in table[0] if c), "")
                heading = _nearest_heading(page.text, first_cell)
                for part in self._table_parts(table):
                    text = f"{header}\n" + (f"{heading}\n" if heading else "") + part
                    chunks.append(
                        Chunk(
                            chunk_id=f"{profile.document_id}:p{page.page}:t{t_index}",
                            document_id=profile.document_id,
                            page=page.page,
                            chunk_type="table",
                            text=text,
                            metadata={**meta, "page": page.page, "chunk_type": "table"},
                            content_hash=_content_hash(text),
                        )
                    )
                    t_index += 1
        return chunks

    def _table_parts(self, rows: list[list[str]]) -> list[str]:
        full = table_to_markdown(rows)
        limit = self.options.chunk_chars * 2
        if len(full) <= limit:
            return [full]
        header, body = rows[0], rows[1:]
        parts: list[str] = []
        current: list[list[str]] = []
        for row in body:
            candidate = table_to_markdown([header, *current, row])
            if current and len(candidate) > limit:
                parts.append(table_to_markdown([header, *current]))
                current = [row]
            else:
                current.append(row)
        if current:
            parts.append(table_to_markdown([header, *current]))
        return parts
