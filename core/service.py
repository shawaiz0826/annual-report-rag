"""ChatService — the single entry point used by the CLI, API, UI and eval harness.

Composes every component from :class:`~core.config.Settings`. Nothing outside ``core``
constructs pipelines, indexes or agents directly.
"""

from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path
from typing import Any

from core.config import Settings, get_settings
from core.facts.extractor import FactExtractor
from core.facts.registry import Registry, load_registry
from core.facts.store import FactStore
from core.index.catalog import Catalog
from core.index.embeddings import EmbeddingModel, build_embedding_model
from core.index.hybrid import HybridRetriever
from core.index.indexer import Indexer
from core.index.lexical import BM25Index, LexicalIndex
from core.index.vector_store import ChromaVectorStore, VectorStore
from core.ingestion.pipeline import IngestionPipeline, IngestReport
from core.ingestion.store import DocumentStore
from core.llm.client import LLMProvider, build_provider
from core.models import RetrievedChunk
from core.observability.logging import configure_logging

log = logging.getLogger(__name__)


class ChatService:
    """Facade over ingestion, retrieval, facts and the agent."""

    def __init__(self, settings: Settings | None = None, provider: LLMProvider | None = None):
        self.settings = settings or get_settings()
        configure_logging(self.settings.log_level)
        self._provider = provider

    # ------------------------------------------------------------------ components

    @cached_property
    def provider(self) -> LLMProvider:
        """The LLM provider (lazily built so offline commands never need a key)."""
        return self._provider or build_provider(self.settings)

    @cached_property
    def catalog(self) -> Catalog:
        """The document catalog."""
        return Catalog(self.settings.paths.derived_dir / "catalog.json")

    @cached_property
    def store(self) -> DocumentStore:
        """Cleaned pages and chunks on disk."""
        return DocumentStore(self.settings.paths.index_dir / "docs")

    @cached_property
    def embedding(self) -> EmbeddingModel:
        """Dense encoder (loads the model on first use)."""
        return build_embedding_model(self.settings.embeddings)

    @cached_property
    def vector_store(self) -> VectorStore:
        """Persistent vector store."""
        return ChromaVectorStore(
            self.settings.paths.index_dir / "chroma", self.settings.vector_store.collection
        )

    @cached_property
    def lexical(self) -> LexicalIndex:
        """BM25 index."""
        return BM25Index(self.settings.paths.index_dir / "bm25.pkl", self.settings.lexical)

    @cached_property
    def indexer(self) -> Indexer:
        """Indexing stage of the pipeline."""
        return Indexer(self.embedding, self.vector_store, self.lexical)

    @cached_property
    def registry(self) -> Registry:
        """Metric registry."""
        return load_registry()

    @cached_property
    def fact_store(self) -> FactStore:
        """SQLite facts store."""
        return FactStore(self.settings.paths.index_dir / "facts.db")

    @cached_property
    def fact_extractor(self) -> FactExtractor:
        """Facts stage of the pipeline."""
        return FactExtractor(
            self.provider,
            self.registry,
            self.retriever,
            self.fact_store,
            self.settings.paths.index_dir / "facts_raw",
            self.settings.paths.derived_dir / "facts_export.csv",
            self.settings.facts,
        )

    @cached_property
    def retriever(self) -> HybridRetriever:
        """Hybrid dense + lexical retriever."""
        return HybridRetriever(
            self.embedding, self.vector_store, self.lexical, self.store, self.settings.retrieval
        )

    # ------------------------------------------------------------------ operations

    def ingest(self, path: Path | None = None, force: bool = False) -> IngestReport:
        """Run the ingestion pipeline over ``path`` (default: configured raw_dir)."""
        pipeline = IngestionPipeline(
            self.settings,
            self.provider,
            self.catalog,
            self.store,
            indexer=self.indexer,
            facts=self.fact_extractor,
        )
        return pipeline.run(path or self.settings.paths.raw_dir, force=force)

    def extract_facts(self, force: bool = False) -> dict[str, int]:
        """(Re)run fact extraction for catalogued financial documents without re-profiling."""
        counts: dict[str, int] = {}
        for profile in self.catalog.financial_documents():
            raw_path = self.settings.paths.index_dir / "facts_raw" / f"{profile.document_id}.json"
            if raw_path.exists() and not force:
                continue
            pages = self.store.load_pages(profile.document_id)
            chunks = self.store.load_chunks(profile.document_id)
            n, _usage = self.fact_extractor.extract_document(profile, pages, chunks)
            self.catalog.set_stats(profile.document_id, facts=n)
            counts[profile.file_name] = n
        self.fact_extractor.finalise()
        self.catalog.save()
        return counts

    def reindex(self) -> dict[str, int]:
        """Rebuild the vector and BM25 indexes from stored chunks (no LLM calls)."""
        counts: dict[str, int] = {}
        for profile in self.catalog.documents():
            if not self.store.has(profile.document_id):
                log.warning("%s has no stored chunks; re-ingest it", profile.file_name)
                continue
            chunks = self.store.load_chunks(profile.document_id)
            self.indexer.index_document(profile, chunks)
            counts[profile.file_name] = len(chunks)
        return counts

    def search(
        self,
        query: str,
        issuers: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Hybrid search (used by the search tool and `scripts/search.py`)."""
        return self.retriever.search(query, filters=filters, k=k, issuers=issuers)

    def catalog_view(self) -> dict:
        """Catalog structure for tools and UIs."""
        return self.catalog.describe()

    def index_stats(self) -> dict[str, int]:
        """Counts for /health and the CLI."""
        return {
            "documents": len(self.catalog.documents()),
            "chunks": self.lexical.count(),
            "vectors": self.vector_store.count(),
            "facts": self.fact_store.count(),
        }
