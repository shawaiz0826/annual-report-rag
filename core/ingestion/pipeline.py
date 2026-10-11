"""Ingestion pipeline: hash → load → clean → profile → chunk → store → index → facts → catalog.

Idempotent (keyed on content hash) and fault-isolated: one bad document is reported,
the rest proceed. Indexing and fact extraction are injected stages so the pipeline
has no knowledge of their implementations.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from core.config import Settings
from core.index.catalog import Catalog
from core.ingestion.chunker import ChunkingOptions, ChunkingStrategy, FixedWindowChunker
from core.ingestion.cleaner import CleaningOptions, clean_pages
from core.ingestion.loaders.base import LoaderRegistry
from core.ingestion.loaders.registry import default_registry
from core.ingestion.profiler import DocumentProfiler, document_id_for, resolve_source_path
from core.ingestion.store import DocumentStore
from core.llm.client import LLMProvider
from core.models import Chunk, DocumentProfile, PageText, Usage

log = logging.getLogger(__name__)


class IndexStage(Protocol):
    """Adds chunks to the search indexes."""

    def index_document(self, profile: DocumentProfile, chunks: list[Chunk]) -> None:
        """Index (or re-index) one document's chunks."""
        ...

    def delete_document(self, document_id: str) -> None:
        """Remove a document from the indexes."""
        ...


class FactsStage(Protocol):
    """Extracts financial facts from a document."""

    def extract_document(
        self, profile: DocumentProfile, pages: list[PageText], chunks: list[Chunk]
    ) -> tuple[int, Usage]:
        """Extract and store facts; returns (fact count, LLM usage)."""
        ...

    def delete_document(self, document_id: str) -> None:
        """Remove a document's facts."""
        ...

    def finalise(self) -> None:
        """Run after all documents (reconciliation, export)."""
        ...


@dataclass
class DocumentResult:
    """Outcome of ingesting one file."""

    file_name: str
    status: str  # ingested | skipped | failed
    document_id: str = ""
    legal_entity: str | None = None
    doc_type: str | None = None
    fiscal_year: int | None = None
    pages: int = 0
    pages_kept: int = 0
    chunks: int = 0
    facts: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    usage: Usage = field(default_factory=Usage)


@dataclass
class IngestReport:
    """Outcome of a whole run."""

    results: list[DocumentResult] = field(default_factory=list)

    @property
    def usage(self) -> Usage:
        """Total LLM usage across all documents."""
        total = Usage()
        for r in self.results:
            total = total + r.usage
        return total

    def counts(self) -> dict[str, int]:
        """Number of documents per status."""
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def as_dict(self) -> dict[str, Any]:
        """Serialisable summary."""
        return {
            "counts": self.counts(),
            "usage": self.usage.model_dump(),
            "documents": [r.__dict__ | {"usage": r.usage.model_dump()} for r in self.results],
        }


class IngestionPipeline:
    """Orchestrates ingestion for a directory tree."""

    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider,
        catalog: Catalog,
        store: DocumentStore,
        *,
        loaders: LoaderRegistry | None = None,
        chunker: ChunkingStrategy | None = None,
        indexer: IndexStage | None = None,
        facts: FactsStage | None = None,
        profiler_model: str | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.store = store
        self.loaders = loaders or default_registry()
        self.chunker = chunker or FixedWindowChunker(
            ChunkingOptions(
                chunk_chars=settings.chunking.chunk_chars,
                overlap_chars=settings.chunking.overlap_chars,
                table_chunks=settings.chunking.table_chunks,
                min_table_rows=settings.chunking.min_table_rows,
            )
        )
        ing = settings.ingestion
        self.cleaning = CleaningOptions(
            dedupe_lines=ing.dedupe_lines,
            header_footer_min_page_ratio=ing.header_footer_min_page_ratio,
            header_footer_long_line_ratio=ing.header_footer_long_line_ratio,
            header_footer_long_line_chars=ing.header_footer_long_line_chars,
            header_footer_zone_lines=ing.header_footer_zone_lines,
            min_pages_for_header_detection=ing.min_pages_for_header_detection,
            min_chunk_chars=ing.min_chunk_chars,
        )
        self.profiler = DocumentProfiler(provider, ing, model=profiler_model)
        self.indexer = indexer
        self.facts = facts

    # ------------------------------------------------------------------ public

    def discover(self, root: Path) -> list[Path]:
        """All supported files under ``root`` (recursive, sorted). Folder names carry no meaning."""
        if root.is_file():
            return [root] if self.loaders.supports(root) else []
        return sorted(p for p in root.rglob("*") if p.is_file() and self.loaders.supports(p))

    def run(self, root: Path, force: bool = False) -> IngestReport:
        """Ingest every supported file under ``root``."""
        report = IngestReport()
        files = self.discover(root)
        log.info("ingesting %d file(s) from %s", len(files), root)
        for path in files:
            report.results.append(self.ingest_file(path, force=force))
        if self.facts is not None and any(r.status == "ingested" for r in report.results):
            self.facts.finalise()
        self.catalog.save()
        return report

    def ingest_file(self, path: Path, force: bool = False) -> DocumentResult:
        """Ingest one file; never raises."""
        started = time.perf_counter()
        result = DocumentResult(file_name=path.name, status="failed")
        try:
            document_id = document_id_for(path)
            result.document_id = document_id
            if self.catalog.has(document_id) and self.store.has(document_id) and not force:
                existing = self.catalog.get(document_id)
                stats = self.catalog.stats(document_id)
                result.status = "skipped"
                result.legal_entity = existing.legal_entity if existing else None
                result.doc_type = existing.doc_type if existing else None
                result.fiscal_year = existing.fiscal_year if existing else None
                result.pages = existing.page_count if existing else 0
                result.chunks = int(stats.get("chunks", 0))
                result.facts = int(stats.get("facts", 0))
                return result
            if force or self.catalog.has(document_id):
                self._forget(document_id)

            raw_pages = self.loaders.get(path.suffix).load(path)
            pages = clean_pages(raw_pages, self.cleaning)
            profile, usage = self.profiler.profile(path, document_id, pages, len(raw_pages))
            result.usage = result.usage + usage
            chunks = self.chunker.chunk(pages, profile)
            self.store.save(document_id, pages, chunks)
            if self.indexer is not None:
                self.indexer.index_document(profile, chunks)
            facts_count = 0
            if self.facts is not None and profile.is_financial:
                facts_count, f_usage = self.facts.extract_document(profile, pages, chunks)
                result.usage = result.usage + f_usage
            self.catalog.add(
                profile,
                stats={
                    "pages": len(raw_pages),
                    "pages_kept": len(pages),
                    "chunks": len(chunks),
                    "table_chunks": sum(1 for c in chunks if c.chunk_type == "table"),
                    "facts": facts_count,
                    "chars": sum(len(p.text) for p in pages),
                },
            )
            self.catalog.save()
            result.status = "ingested"
            result.legal_entity = profile.legal_entity
            result.doc_type = profile.doc_type
            result.fiscal_year = profile.fiscal_year
            result.pages = len(raw_pages)
            result.pages_kept = len(pages)
            result.chunks = len(chunks)
            result.facts = facts_count
            result.warnings = list(profile.warnings)
        except Exception as exc:  # fault isolation: report, do not abort the run
            log.exception("failed to ingest %s", path)
            result.error = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            result.seconds = round(time.perf_counter() - started, 1)
        return result

    def reprocess_document(self, profile: DocumentProfile) -> int:
        """Re-run load → clean → chunk → store → index for one catalogued document (no LLM)."""
        path = resolve_source_path(
            profile.source_path, profile.file_name, self.settings.paths.raw_dir
        )
        raw_pages = self.loaders.get(path.suffix).load(path)
        pages = clean_pages(raw_pages, self.cleaning)
        chunks = self.chunker.chunk(pages, profile)
        self.store.save(profile.document_id, pages, chunks)
        if self.indexer is not None:
            self.indexer.index_document(profile, chunks)
        self.catalog.set_stats(
            profile.document_id,
            pages=len(raw_pages),
            pages_kept=len(pages),
            chunks=len(chunks),
            table_chunks=sum(1 for c in chunks if c.chunk_type == "table"),
            chars=sum(len(p.text) for p in pages),
        )
        return len(chunks)

    # ------------------------------------------------------------------ internals

    def _forget(self, document_id: str) -> None:
        if self.indexer is not None:
            self.indexer.delete_document(document_id)
        if self.facts is not None:
            self.facts.delete_document(document_id)
        self.store.delete(document_id)
        self.catalog.remove(document_id)
