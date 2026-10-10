"""Vector store protocol and the Chroma implementation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from core.models import Chunk

log = logging.getLogger(__name__)

Filters = dict[str, Any] | None
"""Metadata field → value or list of allowed values."""


class VectorStore(Protocol):
    """Approximate nearest-neighbour store keyed by chunk id."""

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Insert or replace chunks with their vectors."""
        ...

    def query(
        self, vector: list[float], k: int, filters: Filters = None
    ) -> list[tuple[str, float]]:
        """Return ``[(chunk_id, score), ...]`` best first; score is a similarity in [0, 1]."""
        ...

    def get(self, chunk_ids: list[str]) -> list[Chunk]:
        """Fetch chunks by id (order not guaranteed)."""
        ...

    def count(self) -> int:
        """Number of stored chunks."""
        ...

    def delete_document(self, document_id: str) -> None:
        """Remove every chunk of a document."""
        ...


def to_chroma_where(filters: Filters) -> dict[str, Any] | None:
    """Translate ``{field: value | [values]}`` to a Chroma ``where`` clause."""
    if not filters:
        return None
    clauses: list[dict[str, Any]] = []
    for field, value in filters.items():
        if value is None:
            continue
        if isinstance(value, list | tuple | set):
            values = list(value)
            if not values:
                continue
            clauses.append({field: {"$in": values}} if len(values) > 1 else {field: values[0]})
        else:
            clauses.append({field: value})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class ChromaVectorStore:
    """Persistent local Chroma collection with cosine similarity."""

    def __init__(self, path: Path, collection: str) -> None:
        import chromadb  # heavy import, keep local

        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}
        )

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Upsert in batches of 256."""
        if not chunks:
            return
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors differ in length")
        for start in range(0, len(chunks), 256):
            batch = chunks[start : start + 256]
            self._collection.upsert(
                ids=[c.chunk_id for c in batch],
                embeddings=vectors[start : start + 256],
                documents=[c.text for c in batch],
                metadatas=[_clean_metadata(c.metadata) for c in batch],
            )

    def query(
        self, vector: list[float], k: int, filters: Filters = None
    ) -> list[tuple[str, float]]:
        """Cosine kNN; returns similarity = 1 - distance."""
        if k <= 0 or self.count() == 0:
            return []
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=min(k, self.count()),
            where=to_chroma_where(filters),
            include=["distances"],
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [(cid, 1.0 - float(d)) for cid, d in zip(ids, distances, strict=True)]

    def get(self, chunk_ids: list[str]) -> list[Chunk]:
        """Rebuild Chunk objects from stored documents and metadata."""
        if not chunk_ids:
            return []
        result = self._collection.get(ids=chunk_ids, include=["documents", "metadatas"])
        chunks: list[Chunk] = []
        for cid, text, meta in zip(
            result["ids"], result["documents"], result["metadatas"], strict=True
        ):
            meta = dict(meta or {})
            chunks.append(
                Chunk(
                    chunk_id=cid,
                    document_id=str(meta.get("document_id", "")),
                    page=int(meta.get("page", 1)),
                    chunk_type=str(meta.get("chunk_type", "text")),  # type: ignore[arg-type]
                    text=text or "",
                    metadata=meta,
                    content_hash=str(meta.get("content_hash", "")),
                )
            )
        return chunks

    def count(self) -> int:
        """Number of stored chunks."""
        return int(self._collection.count())

    def delete_document(self, document_id: str) -> None:
        """Delete all chunks whose metadata document_id matches."""
        self._collection.delete(where={"document_id": document_id})


def _clean_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Chroma accepts only scalar metadata values; drop None, stringify the rest."""
    out: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        out[key] = value if isinstance(value, str | int | float | bool) else str(value)
    return out
