"""Document store: cleaned pages and chunks per document, as JSON under ``data/index/docs``.

The vector store and BM25 index are derived from this; page expansion and fact
extraction read page text from here.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.models import Chunk, PageText


class DocumentStore:
    """One JSON file per document id."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, document_id: str) -> Path:
        return self.root / f"{document_id}.json"

    def save(self, document_id: str, pages: list[PageText], chunks: list[Chunk]) -> None:
        """Persist cleaned pages and chunks."""
        payload = {
            "document_id": document_id,
            "pages": [p.model_dump() for p in pages],
            "chunks": [c.model_dump() for c in chunks],
        }
        self._path(document_id).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def has(self, document_id: str) -> bool:
        """True if the document has been stored."""
        return self._path(document_id).exists()

    def delete(self, document_id: str) -> None:
        """Remove a stored document."""
        self._path(document_id).unlink(missing_ok=True)

    def load_pages(self, document_id: str) -> list[PageText]:
        """Cleaned pages for a document."""
        return [PageText.model_validate(p) for p in self._read(document_id)["pages"]]

    def load_chunks(self, document_id: str) -> list[Chunk]:
        """Chunks for a document."""
        return [Chunk.model_validate(c) for c in self._read(document_id)["chunks"]]

    def page_text(self, document_id: str, page: int) -> str | None:
        """Cleaned text of one page, or None."""
        for p in self.load_pages(document_id):
            if p.page == page:
                return p.text
        return None

    def document_ids(self) -> list[str]:
        """All stored document ids."""
        return sorted(p.stem for p in self.root.glob("*.json"))

    def all_chunks(self) -> list[Chunk]:
        """Every chunk of every stored document."""
        chunks: list[Chunk] = []
        for document_id in self.document_ids():
            chunks.extend(self.load_chunks(document_id))
        return chunks

    def _read(self, document_id: str) -> dict:
        return json.loads(self._path(document_id).read_text(encoding="utf-8"))
