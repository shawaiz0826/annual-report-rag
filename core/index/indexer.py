"""The indexing stage of the ingestion pipeline: embed → vector store; tokenise → BM25."""

from __future__ import annotations

import logging

from core.index.embeddings import EmbeddingModel
from core.index.lexical import LexicalIndex
from core.index.vector_store import VectorStore
from core.models import Chunk, DocumentProfile

log = logging.getLogger(__name__)


class Indexer:
    """Implements :class:`core.ingestion.pipeline.IndexStage`."""

    def __init__(self, embedding: EmbeddingModel, vector_store: VectorStore, lexical: LexicalIndex):
        self.embedding = embedding
        self.vector_store = vector_store
        self.lexical = lexical

    def index_document(self, profile: DocumentProfile, chunks: list[Chunk]) -> None:
        """Replace any previous version of the document in both indexes."""
        self.vector_store.delete_document(profile.document_id)
        self.lexical.delete_document(profile.document_id)
        if not chunks:
            return
        vectors = self.embedding.embed_documents([c.text for c in chunks])
        self.vector_store.upsert(chunks, vectors)
        self.lexical.add(chunks)
        self.lexical.save()
        log.info("indexed %s: %d chunks", profile.file_name, len(chunks))

    def delete_document(self, document_id: str) -> None:
        """Remove a document from both indexes."""
        self.vector_store.delete_document(document_id)
        self.lexical.delete_document(document_id)
        self.lexical.save()
