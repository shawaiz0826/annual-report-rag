"""LLM fact extraction: page selection via retrieval, then per-page structured extraction."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from core.agent.prompts import load_prompt
from core.config import FactsConfig
from core.facts.reconcile import reconcile
from core.facts.registry import Registry, normalise_alias
from core.facts.store import FactStore
from core.index.hybrid import HybridRetriever
from core.llm.client import LLMProvider, QuotaExhaustedError, structured
from core.models import (
    UNIT_MULTIPLIER,
    Chunk,
    DocumentProfile,
    FactRecord,
    PageText,
    ReportingUnit,
    Usage,
)

log = logging.getLogger(__name__)


class ExtractedItem(BaseModel):
    """One figure as returned by the model."""

    metric_alias_seen: str = Field(description="The label exactly as printed on the page")
    canonical_metric: str
    fiscal_year: int
    value: float
    unit: ReportingUnit | None = None
    currency: str | None = None
    is_comparative: bool = False
    evidence_snippet: str = Field(
        default="", description="The line the number came from (≤ 120 chars)"
    )


class ExtractionOutput(BaseModel):
    """The JSON object the extractor model must return."""

    items: list[ExtractedItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data: Any) -> Any:
        """Tolerate ``{"items": {"items": [...]}}`` and a bare list — both seen from free models."""
        if isinstance(data, list):
            return {"items": data}
        if isinstance(data, dict):
            inner = data.get("items")
            while isinstance(inner, dict) and "items" in inner:
                inner = inner["items"]
            if inner is not data.get("items"):
                return {**data, "items": inner}
        return data


def select_pages(
    profile: DocumentProfile,
    chunks: list[Chunk],
    registry: Registry,
    retriever: HybridRetriever,
    config: FactsConfig,
) -> list[int]:
    """Pick the pages most likely to carry headline figures.

    Hybrid search restricted to this document for every registry ``search_hint`` votes for
    pages; the top ``pages_per_document`` by votes are taken. Table chunks that contain a
    registry alias add up to ``max_table_pages`` more.
    """
    votes: Counter[int] = Counter()
    for metric in registry.metrics.values():
        for hint in metric.search_hints:
            for hit in retriever.search(hint, filters={"document_id": profile.document_id}, k=5):
                votes[hit.chunk.page] += 1
    pages = [page for page, _ in votes.most_common(config.pages_per_document)]

    aliases = [normalise_alias(a) for m in registry.metrics.values() for a in [m.label, *m.aliases]]
    table_votes: Counter[int] = Counter()
    for chunk in chunks:
        if chunk.chunk_type != "table":
            continue
        body = normalise_alias(chunk.text)
        matched = sum(1 for alias in aliases if alias in body)
        if matched:
            table_votes[chunk.page] += matched
    for page, _ in table_votes.most_common():
        if len(pages) >= config.pages_per_document + config.max_table_pages:
            break
        if page not in pages:
            pages.append(page)
    return sorted(pages)


def _registry_listing(registry: Registry) -> str:
    lines = []
    for metric in registry.metrics.values():
        aliases = ", ".join(metric.aliases[:8])
        lines.append(f"- {metric.name}: {metric.label} (labels seen in reports: {aliases})")
    return "\n".join(lines)


def build_extraction_messages(
    profile: DocumentProfile, page: PageText, registry: Registry
) -> list[dict[str, str]]:
    """Compose the extractor prompt for one page."""
    _, system = load_prompt("extractor")
    header = (
        f"Document profile: legal_entity={profile.legal_entity}; issuer={profile.issuer}; "
        f"fiscal_year={profile.fiscal_year}; currency={profile.reporting_currency}; "
        f"unit={profile.reporting_unit}; entity_type={profile.entity_type}\n\n"
        f"Registry metrics:\n{_registry_listing(registry)}\n\n"
        f"===== PAGE {page.page} =====\n{page.text}"
    )
    if page.tables:
        rendered = []
        for table in page.tables[:6]:
            rendered.append("\n".join(" | ".join(cell for cell in row) for row in table[:40]))
        header += "\n\n===== TABLES ON THIS PAGE (cell grids) =====\n" + "\n\n".join(rendered)
    return [{"role": "system", "content": system}, {"role": "user", "content": header}]


def to_fact_records(
    items: list[ExtractedItem],
    profile: DocumentProfile,
    page: int,
    registry: Registry,
    *,
    model: str,
    prompt_version: str,
) -> tuple[list[FactRecord], list[str]]:
    """Validate model output and map it to :class:`FactRecord`; returns ``(records, warnings)``."""
    records: list[FactRecord] = []
    warnings: list[str] = []
    for item in items:
        metric = registry.resolve(item.canonical_metric) or registry.resolve(item.metric_alias_seen)
        if metric is None:
            warnings.append(
                f"p.{page}: unknown metric {item.canonical_metric!r} ({item.metric_alias_seen!r})"
            )
            continue
        if not 1990 <= item.fiscal_year <= 2100:
            warnings.append(f"p.{page}: implausible fiscal year {item.fiscal_year} for {metric}")
            continue
        unit = item.unit or profile.reporting_unit
        if unit is None:
            warnings.append(f"p.{page}: no unit for {metric} FY{item.fiscal_year}")
            continue
        currency = (item.currency or profile.reporting_currency or "").upper()
        if not currency:
            warnings.append(f"p.{page}: no currency for {metric} FY{item.fiscal_year}")
            continue
        source_year = profile.fiscal_year or item.fiscal_year
        key = f"{profile.document_id}|{page}|{metric}|{item.fiscal_year}|{item.value}"
        records.append(
            FactRecord(
                fact_id=hashlib.sha256(key.encode()).hexdigest()[:12],
                issuer=profile.issuer or "",
                legal_entity=profile.legal_entity or "",
                metric=metric,
                fiscal_year=item.fiscal_year,
                value_reported=float(item.value),
                unit_reported=unit,
                currency=currency,
                value_base=float(item.value) * UNIT_MULTIPLIER[unit],
                document_id=profile.document_id,
                file_name=profile.file_name,
                page=page,
                source_fiscal_year=source_year,
                is_comparative=item.fiscal_year != source_year,
                metric_alias_seen=item.metric_alias_seen,
                evidence_snippet=item.evidence_snippet[:200],
                extraction_model=model,
                extraction_prompt_version=prompt_version,
            )
        )
    return records, warnings


class FactExtractor:
    """Implements :class:`core.ingestion.pipeline.FactsStage`.

    Raw (pre-reconciliation) records are kept per document as JSON so reconciliation can be
    re-run whenever the document set changes; the SQLite store holds the reconciled view.
    """

    def __init__(
        self,
        provider: LLMProvider,
        registry: Registry,
        retriever: HybridRetriever,
        fact_store: FactStore,
        raw_dir: Path,
        export_path: Path,
        config: FactsConfig,
        model: str | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.retriever = retriever
        self.fact_store = fact_store
        self.raw_dir = raw_dir
        self.export_path = export_path
        self.config = config
        self.model = model
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ stage API

    def extract_document(
        self,
        profile: DocumentProfile,
        pages: list[PageText],
        chunks: list[Chunk],
        only_pages: list[int] | None = None,
    ) -> tuple[int, Usage]:
        """Select pages, extract, validate, persist raw records.

        With ``only_pages`` (used to retry failed pages) the given pages are extracted and
        merged into the document's existing raw records instead of replacing them.
        """
        version, _ = load_prompt("extractor")
        if only_pages:
            selected = sorted(only_pages)
            existing = self._load_raw(profile.document_id)
        else:
            selected = select_pages(profile, chunks, self.registry, self.retriever, self.config)
            existing = None
        by_page = {p.page: p for p in pages}
        usage = Usage()
        records: list[FactRecord] = []
        warnings: list[str] = []

        def work(page_no: int) -> tuple[int, list[FactRecord], list[str], Usage]:
            page = by_page.get(page_no)
            if page is None:
                return page_no, [], [], Usage()
            messages = build_extraction_messages(profile, page, self.registry)
            try:
                output, call_usage, model = structured(
                    self.provider,
                    messages,
                    ExtractionOutput,
                    schema_name="extracted_facts",
                    model=self.model,
                    max_tokens=6000,
                )
            except QuotaExhaustedError:
                raise  # stop the whole run: retrying other pages cannot succeed
            except Exception as exc:  # one bad page must not sink the document
                log.warning("%s p.%d: extraction failed: %s", profile.file_name, page_no, exc)
                return page_no, [], [f"p.{page_no}: extraction failed"], Usage()
            page_records, page_warnings = to_fact_records(
                output.items, profile, page_no, self.registry, model=model, prompt_version=version
            )
            return page_no, page_records, page_warnings, call_usage

        workers = max(1, self.config.extraction_concurrency)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            try:
                for _page_no, page_records, page_warnings, call_usage in pool.map(work, selected):
                    records.extend(page_records)
                    warnings.extend(page_warnings)
                    usage = usage + call_usage
            except QuotaExhaustedError:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
        if existing is not None:
            retried = set(selected)
            kept = [r for r in existing["records"] if r.page not in retried]
            records = kept + records
            warnings = [
                w for w in existing["warnings"] if not any(w.startswith(f"p.{p}:") for p in retried)
            ] + warnings
            selected = sorted(set(existing["pages_selected"]) | retried)
        self._save_raw(profile.document_id, records, selected, warnings)
        log.info(
            "%s: %d raw facts from pages %s (%d warnings)",
            profile.file_name,
            len(records),
            selected,
            len(warnings),
        )
        return len(records), usage

    def delete_document(self, document_id: str) -> None:
        """Forget a document's raw records (reconciled view refreshed by :meth:`finalise`)."""
        (self.raw_dir / f"{document_id}.json").unlink(missing_ok=True)
        self.fact_store.delete_document(document_id)

    def finalise(self) -> None:
        """Reconcile every raw record across documents, rebuild the store, export CSV."""
        raw: list[FactRecord] = []
        for path in sorted(self.raw_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw.extend(FactRecord.model_validate(r) for r in payload["records"])
        reconciled = reconcile(
            raw,
            tolerance_pct=self.config.agreement_tolerance_pct,
            min_confidence=self.config.min_confidence_to_use,
        )
        self.fact_store.replace_all(reconciled)
        count = self.fact_store.export_csv(self.export_path)
        log.info(
            "facts reconciled: %d raw → %d stored; exported %d rows",
            len(raw),
            len(reconciled),
            count,
        )

    # ------------------------------------------------------------------ helpers

    def _save_raw(
        self, document_id: str, records: list[FactRecord], pages: list[int], warnings: list[str]
    ) -> None:
        payload: dict[str, Any] = {
            "document_id": document_id,
            "pages_selected": pages,
            "warnings": warnings,
            "records": [r.model_dump(mode="json") for r in records],
        }
        (self.raw_dir / f"{document_id}.json").write_text(
            json.dumps(payload, indent=1), encoding="utf-8"
        )

    def _load_raw(self, document_id: str) -> dict[str, Any]:
        path = self.raw_dir / f"{document_id}.json"
        if not path.exists():
            return {"records": [], "warnings": [], "pages_selected": []}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "records": [FactRecord.model_validate(r) for r in payload.get("records", [])],
            "warnings": list(payload.get("warnings", [])),
            "pages_selected": list(payload.get("pages_selected", [])),
        }

    def failed_pages(self, document_id: str) -> list[int]:
        """Pages whose extraction failed in the stored raw results."""
        out: list[int] = []
        for w in self.raw_warnings(document_id):
            m = re.match(r"p\.(\d+): extraction failed", w)
            if m:
                out.append(int(m.group(1)))
        return sorted(set(out))

    def raw_warnings(self, document_id: str) -> list[str]:
        """Warnings recorded for a document's extraction."""
        path = self.raw_dir / f"{document_id}.json"
        if not path.exists():
            return []
        return list(json.loads(path.read_text(encoding="utf-8")).get("warnings", []))

    def is_complete(self, document_id: str) -> bool:
        """True when raw facts exist and no page failed."""
        path = self.raw_dir / f"{document_id}.json"
        return path.exists() and not any(
            "extraction failed" in w for w in self.raw_warnings(document_id)
        )
