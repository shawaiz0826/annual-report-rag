"""Lexical index protocol and the BM25 implementation (rank-bm25, pickled to disk)."""

from __future__ import annotations

import logging
import pickle
import re
from pathlib import Path
from typing import Any, Protocol

from core.config import LexicalConfig
from core.models import Chunk

log = logging.getLogger(__name__)

_SPLIT = re.compile(r"[^0-9a-z]+")
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def tokenize(text: str) -> list[str]:
    """Lower-case tokens; numbers with thousands separators are added in both forms.

    ``"Revenues 96,773"`` → ``["revenues", "96,773"→"96773", "96", "773"]`` so a query for
    ``96773`` or ``96,773`` both match. Tokens are kept when ≥ 2 chars or pure digits.
    """
    lowered = text.lower()
    extra = [m.group(0).replace(",", "") for m in _THOUSANDS.finditer(lowered)]
    tokens = [t for t in _SPLIT.split(lowered) if t and (len(t) >= 2 or t.isdigit())]
    return tokens + extra


def matches_filters(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    """Post-filter: every filter field must equal the value or be one of the listed values."""
    if not filters:
        return True
    for field, wanted in filters.items():
        if wanted is None:
            continue
        actual = metadata.get(field)
        if isinstance(wanted, list | tuple | set):
            if not wanted:
                continue
            if actual not in wanted:
                return False
        elif actual != wanted:
            return False
    return True


class LexicalIndex(Protocol):
    """Keyword index over chunks."""

    def add(self, chunks: list[Chunk]) -> None:
        """Add (or replace) chunks."""
        ...

    def query(
        self, text: str, k: int, filters: dict[str, Any] | None = None
    ) -> list[tuple[str, float]]:
        """Return ``[(chunk_id, score), ...]`` best first."""
        ...

    def get(self, chunk_ids: list[str]) -> list[Chunk]:
        """Fetch stored chunks by id."""
        ...

    def delete_document(self, document_id: str) -> None:
        """Remove a document's chunks."""
        ...

    def count(self) -> int:
        """Number of indexed chunks."""
        ...

    def save(self) -> None:
        """Persist."""
        ...

    def load(self) -> None:
        """Load from disk if present."""
        ...


class BM25Index:
    """rank-bm25 over tokenised chunks; rebuilt on every add (cheap at this scale)."""

    def __init__(self, path: Path, config: LexicalConfig) -> None:
        self.path = path
        self.k1 = config.k1
        self.b = config.b
        self._chunks: dict[str, Chunk] = {}
        self._order: list[str] = []
        self._bm25: Any = None
        self.load()

    # ------------------------------------------------------------------ persistence

    def load(self) -> None:
        """Load chunks and rebuild the BM25 model."""
        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            payload = pickle.load(fh)  # noqa: S301 - local file we wrote ourselves
        self._chunks = {c["chunk_id"]: Chunk.model_validate(c) for c in payload["chunks"]}
        self._order = list(self._chunks)
        self._rebuild()

    def save(self) -> None:
        """Pickle chunks (the model is rebuilt on load)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as fh:
            pickle.dump({"chunks": [c.model_dump() for c in self._chunks.values()]}, fh)

    # ------------------------------------------------------------------ mutation

    def add(self, chunks: list[Chunk]) -> None:
        """Add or replace chunks and rebuild."""
        for chunk in chunks:
            self._chunks[chunk.chunk_id] = chunk
        self._order = list(self._chunks)
        self._rebuild()

    def delete_document(self, document_id: str) -> None:
        """Remove every chunk of ``document_id``."""
        self._chunks = {k: v for k, v in self._chunks.items() if v.document_id != document_id}
        self._order = list(self._chunks)
        self._rebuild()

    # ------------------------------------------------------------------ queries

    def query(
        self, text: str, k: int, filters: dict[str, Any] | None = None
    ) -> list[tuple[str, float]]:
        """BM25 scores, post-filtered on metadata; zero-score hits are dropped."""
        if self._bm25 is None or k <= 0:
            return []
        tokens = tokenize(text)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(self._order)), key=lambda i: scores[i], reverse=True)
        out: list[tuple[str, float]] = []
        for i in ranked:
            if scores[i] <= 0:
                break
            chunk = self._chunks[self._order[i]]
            if matches_filters(chunk.metadata, filters):
                out.append((chunk.chunk_id, float(scores[i])))
                if len(out) >= k:
                    break
        return out

    def get(self, chunk_ids: list[str]) -> list[Chunk]:
        """Stored chunks by id (missing ids skipped)."""
        return [self._chunks[c] for c in chunk_ids if c in self._chunks]

    def count(self) -> int:
        """Number of indexed chunks."""
        return len(self._chunks)

    def _rebuild(self) -> None:
        if not self._order:
            self._bm25 = None
            return
        from rank_bm25 import BM25Okapi

        corpus = [tokenize(self._chunks[cid].text) for cid in self._order]
        self._bm25 = BM25Okapi(corpus, k1=self.k1, b=self.b)
