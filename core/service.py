"""ChatService — the single entry point used by the CLI, API, UI and eval harness.

Composes every component from :class:`~core.config.Settings`. Nothing outside ``core``
constructs pipelines, indexes or agents directly.
"""

from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path
from typing import Any

from core.agent.agent import Agent
from core.agent.memory import SessionStore, build_session_store, slots_from_answer
from core.agent.tools import ToolBox
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
from core.models import AgentAnswer, RetrievedChunk, Session, Trace, Turn
from core.observability.logging import configure_logging
from core.observability.replay import ReplayCache
from core.observability.tracing import TraceStore, render_trace

log = logging.getLogger(__name__)


class ChatService:
    """Facade over ingestion, retrieval, facts and the agent."""

    def __init__(
        self,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        session_store: SessionStore | None = None,
    ):
        self.settings = settings or get_settings()
        configure_logging(self.settings.log_level)
        self._provider = provider
        self._session_store = session_store
        self.replay_enabled = False
        self.record_enabled = False

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

    @cached_property
    def sessions(self) -> SessionStore:
        """Session store (SQLite by default, in-memory for tests/eval)."""
        return self._session_store or build_session_store(
            self.settings.sessions.backend, self.settings.sessions.path
        )

    @cached_property
    def traces(self) -> TraceStore:
        """Trace store."""
        return TraceStore(self.settings.paths.traces_dir)

    @cached_property
    def replay(self) -> ReplayCache:
        """Replay cache for deterministic demos."""
        return ReplayCache(self.settings.paths.replay_cache)

    @cached_property
    def agent(self) -> Agent:
        """The tool-calling agent (the retriever — and its embedding model — load on first search)."""
        lazy_retriever = _Lazy(lambda: self.retriever)
        return Agent(
            self.provider,
            lambda: ToolBox(self.catalog, lazy_retriever, self.fact_store, self.registry),
            self.catalog,
            self.settings.agent,
            self.traces,
        )

    # ------------------------------------------------------------------ chat

    def new_session(self) -> Session:
        """Start a conversation."""
        return self.sessions.create()

    def chat(
        self,
        session_id: str | None,
        message: str,
        *,
        model: str | None = None,
        show_model_knowledge: bool | None = None,
    ) -> tuple[AgentAnswer, Trace]:
        """Answer one message; persists the turn, slot memory and trace."""
        session = (self.sessions.get(session_id) if session_id else None) or self.sessions.create()
        model_id = model or self.settings.llm_model
        show = (
            self.settings.agent.show_model_knowledge
            if show_model_knowledge is None
            else show_model_knowledge
        )
        key = self.replay.key(model_id, session, message, show)
        cached = self.replay.get(key) if self.replay_enabled else None
        if cached is not None:
            answer, trace = cached
            trace = trace.model_copy(update={"replayed": True, "session_id": session.session_id})
            self.traces.write(trace)
        else:
            answer, trace = self.agent.run(
                session, message, model=model_id, show_model_knowledge=show
            )
            if self.record_enabled and trace.error is None:
                self.replay.put(key, answer, trace)
        self.sessions.append(session.session_id, Turn(role="user", content=message))
        self.sessions.append(
            session.session_id,
            Turn(
                role="assistant",
                content=answer.answer_markdown,
                answer=answer,
                trace_id=trace.trace_id,
            ),
        )
        self.sessions.update_slots(session.session_id, slots_from_answer(answer, session.slots))
        return answer, trace

    def explain(self, trace_id: str | None = None) -> str:
        """Human-readable rendering of a trace (default: the latest)."""
        trace = self.traces.get(trace_id) if trace_id else self.traces.latest
        if trace is None:
            return "no trace found"
        return render_trace(trace)

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
        """(Re)run fact extraction for catalogued financial documents without re-profiling.

        Documents whose raw facts are complete are skipped unless ``force``; documents with
        failed pages are re-extracted. A quota error stops the run (partial results are kept
        and reconciled) and is re-raised so the caller can report it.
        """
        counts: dict[str, int] = {}
        try:
            for profile in self.catalog.financial_documents():
                if self.fact_extractor.is_complete(profile.document_id) and not force:
                    continue
                pages = self.store.load_pages(profile.document_id)
                chunks = self.store.load_chunks(profile.document_id)
                retry = None if force else self.fact_extractor.failed_pages(profile.document_id)
                n, _usage = self.fact_extractor.extract_document(
                    profile, pages, chunks, only_pages=retry or None
                )
                self.catalog.set_stats(profile.document_id, facts=n)
                counts[profile.file_name] = n
        finally:
            self.fact_extractor.finalise()
            self.catalog.save()
        return counts

    def reprocess(self) -> dict[str, int]:
        """Re-load, re-clean, re-chunk and re-index every catalogued file, keeping its profile.

        Use after changing cleaning/chunking settings; no LLM calls are made.
        """
        pipeline = IngestionPipeline(
            self.settings, self.provider, self.catalog, self.store, indexer=self.indexer
        )
        counts: dict[str, int] = {}
        for profile in self.catalog.documents():
            counts[profile.file_name] = pipeline.reprocess_document(profile)
        self.catalog.save()
        return counts

    def import_facts(self) -> int:
        """Load facts from the committed CSV export (no LLM calls)."""
        return self.fact_store.import_csv(self.settings.paths.derived_dir / "facts_export.csv")

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


class _Lazy:
    """Defer construction of a heavy component until an attribute is first used."""

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self._target: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._target is None:
            self._target = self._factory()
        return getattr(self._target, name)
