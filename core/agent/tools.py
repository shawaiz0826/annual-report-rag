"""Agent tools: typed inputs, JSON schemas for the model, and the executor.

Every tool result is stored in full in the trace and truncated for the model.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from core.facts.compute import ComputeError, CurrencyMismatch, compute
from core.facts.registry import Registry
from core.facts.store import FactStore
from core.index.catalog import Catalog
from core.index.hybrid import HybridRetriever
from core.models import AgentAnswer, Citation, Confidence, FactRecord, RetrievedChunk

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- tool inputs


class ListCatalogInput(BaseModel):
    """No arguments."""


class SearchDocumentsInput(BaseModel):
    """Arguments for ``search_documents``."""

    query: str = Field(
        description="Natural-language or keyword query; include the metric, company and year words."
    )
    issuers: list[str] | None = Field(
        default=None, description="Restrict to these issuers (brand names from the catalog)."
    )
    legal_entities: list[str] | None = Field(
        default=None, description="Restrict to these legal entities."
    )
    fiscal_years: list[int] | None = Field(
        default=None,
        description="Advisory filter on the DOCUMENT's fiscal year. Prior-year figures live inside later reports, so usually leave unset.",
    )
    doc_types: list[str] | None = Field(
        default=None, description="annual_report | financial_statements | news | other"
    )
    k: int | None = Field(default=None, description="Number of results (default from config).")


class LookupFactsInput(BaseModel):
    """Arguments for ``lookup_facts``."""

    metric: str = Field(
        description="Metric name or alias, e.g. 'revenue', 'net income', 'profit before tax'."
    )
    issuer: str | None = Field(
        default=None, description="Issuer / brand, e.g. the company name as the user says it."
    )
    legal_entity: str | None = Field(
        default=None, description="Exact legal entity, if the user asked for a specific one."
    )
    fiscal_years: list[int] | None = Field(
        default=None, description="Fiscal years wanted; omit for all available."
    )


class ComputeMetricInput(BaseModel):
    """Arguments for ``compute_metric``."""

    derived: str = Field(
        description="Derived metric: net_margin, operating_margin, yoy_growth, cagr, difference, ratio."
    )
    inputs: dict[str, str] = Field(
        description="Formula input name → fact_id taken from lookup_facts results."
    )
    params: dict[str, float] | None = Field(
        default=None, description="Numeric parameters, e.g. {'years': 3} for cagr."
    )


class CitationInput(BaseModel):
    """A citation: just the kind and the id; the rest is filled in by the system."""

    kind: str = Field(description="'fact' or 'chunk'")
    ref: str = Field(description="fact_id (from lookup_facts) or chunk_id (from search_documents)")


class FinalAnswerInput(BaseModel):
    """Arguments for ``final_answer`` — the only way to finish."""

    resolved_question: str = Field(
        description="The self-contained question you answered (follow-ups resolved)."
    )
    answered: bool = Field(
        description="false when the information is not in the provided documents or out of scope."
    )
    answer_markdown: str = Field(
        description="Concise Markdown answer with inline citations like [Entity FY2023 p.12]."
    )
    citations: list[CitationInput] = Field(
        default_factory=list, description="Every fact/chunk the answer relies on."
    )
    caveats: list[str] = Field(
        default_factory=list, description="Entity mismatch, currency mismatch, restatement, gaps."
    )
    confidence: str = Field(default="medium", description="high | medium | low")
    entities_used: list[str] = Field(
        default_factory=list, description="Legal entities the answer is about."
    )
    years_used: list[int] = Field(default_factory=list)
    metrics_used: list[str] = Field(
        default_factory=list, description="Canonical metric names used."
    )
    model_knowledge_note: str | None = Field(
        default=None, description="Only when model knowledge is enabled."
    )


TOOL_INPUTS: dict[str, type[BaseModel]] = {
    "list_catalog": ListCatalogInput,
    "search_documents": SearchDocumentsInput,
    "lookup_facts": LookupFactsInput,
    "compute_metric": ComputeMetricInput,
    "final_answer": FinalAnswerInput,
}

TOOL_DESCRIPTIONS: dict[str, str] = {
    "list_catalog": (
        "List the documents available: issuers, their legal entities (group vs subsidiary), fiscal years, "
        "currency, unit and sector, plus non-financial documents. Call this when scope is unclear or to "
        "explain what is and is not covered."
    ),
    "search_documents": (
        "Hybrid (semantic + keyword) search over document chunks. Returns passages with legal entity, fiscal "
        "year, page and a score. Use it for narrative questions and to corroborate or explain figures. Prior-year "
        "figures appear inside later reports, so do not filter by fiscal year unless the user asked for a specific report."
    ),
    "lookup_facts": (
        "Look up extracted headline figures (revenue, net income, profit before tax, operating income, EBITDA, ...) "
        "by issuer / legal entity / metric / years. Returns values with unit, currency, page, confidence and "
        "reconciliation notes, plus coverage (which entity has which years) so you can explain gaps. Always try this "
        "first for numeric questions."
    ),
    "compute_metric": (
        "Compute a derived figure in Python from fact ids: net_margin (net_income, revenue), operating_margin "
        "(operating_income, revenue), yoy_growth (current, previous), cagr (end, start; params.years), difference (a, b), "
        "ratio (a, b). Never do arithmetic yourself; use this tool. It refuses to combine different currencies."
    ),
    "final_answer": (
        "Finish with the structured answer. Required for every reply. Cite every fact_id / chunk_id you relied on; "
        "state legal entity, fiscal year, currency and unit for every figure; list caveats honestly."
    ),
}


def tool_schemas(names: list[str] | None = None) -> list[dict[str, Any]]:
    """OpenAI-style function schemas for the model."""
    out = []
    for name, model in TOOL_INPUTS.items():
        if names and name not in names:
            continue
        schema = model.model_json_schema()
        schema.pop("title", None)
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "parameters": schema,
                },
            }
        )
    return out


# --------------------------------------------------------------------------- executor


class ToolResult(BaseModel):
    """What a tool returns to the loop."""

    payload: dict[str, Any]
    summary: str
    latency_ms: int
    retrieved: list[dict[str, Any]] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)


class ToolBox:
    """Executes tools against the service components."""

    def __init__(
        self,
        catalog: Catalog,
        retriever: HybridRetriever,
        fact_store: FactStore,
        registry: Registry,
        *,
        search_text_chars: int = 1500,
    ) -> None:
        self.catalog = catalog
        self.retriever = retriever
        self.fact_store = fact_store
        self.registry = registry
        self.search_text_chars = search_text_chars
        self.seen_chunks: dict[str, RetrievedChunk] = {}
        self.seen_facts: dict[str, FactRecord] = {}

    def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate arguments and execute; errors become structured payloads, never exceptions."""
        started = time.perf_counter()
        model = TOOL_INPUTS.get(name)
        if model is None:
            return ToolResult(
                payload={"error": f"unknown tool {name!r}"}, summary="unknown tool", latency_ms=0
            )
        try:
            args = model.model_validate(arguments or {})
        except ValidationError as exc:
            return ToolResult(
                payload={"error": f"invalid arguments: {exc.errors()[:3]}"},
                summary="invalid arguments",
                latency_ms=0,
            )
        handler = getattr(self, f"_{name}")
        try:
            result: ToolResult = handler(args)
        except Exception as exc:  # defensive: the loop must keep going
            log.exception("tool %s failed", name)
            result = ToolResult(
                payload={"error": f"{type(exc).__name__}: {exc}"[:400]},
                summary="tool error",
                latency_ms=0,
            )
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ---------------------------------------------------------------- tools

    def _list_catalog(self, _: ListCatalogInput) -> ToolResult:
        view = self.catalog.describe()
        n = sum(len(v) for v in view["issuers"].values()) + len(view["other_documents"])
        return ToolResult(
            payload=view, summary=f"{n} documents, issuers={list(view['issuers'])}", latency_ms=0
        )

    def _search_documents(self, args: SearchDocumentsInput) -> ToolResult:
        filters: dict[str, Any] = {}
        if args.legal_entities:
            filters["legal_entity"] = args.legal_entities
        if args.fiscal_years:
            filters["fiscal_year"] = args.fiscal_years
        if args.doc_types:
            filters["doc_type"] = args.doc_types
        issuers = None
        unknown: list[str] = []
        if args.issuers:
            issuers = []
            for name in args.issuers:
                resolved = self.catalog.resolve_issuer(name)
                (issuers.append(resolved) if resolved else unknown.append(name))
            if not issuers:
                return ToolResult(
                    payload={
                        "results": [],
                        "error": f"no documents for issuer(s) {unknown}",
                        "known_issuers": list(self.catalog.issuers()),
                    },
                    summary="unknown issuer",
                    latency_ms=0,
                )
        hits = self.retriever.search(args.query, filters=filters or None, k=args.k, issuers=issuers)
        results = []
        retrieved = []
        for hit in hits:
            self.seen_chunks[hit.chunk.chunk_id] = hit
            m = hit.chunk.metadata
            results.append(
                {
                    "chunk_id": hit.chunk.chunk_id,
                    "document_id": hit.chunk.document_id,
                    "file_name": m.get("file_name"),
                    "legal_entity": m.get("legal_entity") or None,
                    "entity_type": m.get("entity_type") or None,
                    "fiscal_year": m.get("fiscal_year") or None,
                    "doc_type": m.get("doc_type"),
                    "page": hit.chunk.page,
                    "rrf_score": round(hit.rrf_score, 4),
                    "text": hit.best_text[: self.search_text_chars],
                }
            )
            retrieved.append(
                {
                    "chunk_id": hit.chunk.chunk_id,
                    "document_id": hit.chunk.document_id,
                    "page": hit.chunk.page,
                    "legal_entity": m.get("legal_entity") or None,
                    "doc_type": m.get("doc_type"),
                    "fiscal_year": m.get("fiscal_year") or None,
                    "dense_rank": hit.dense_rank,
                    "lexical_rank": hit.lexical_rank,
                    "rrf_score": round(hit.rrf_score, 4),
                    "expanded": hit.expanded_text is not None,
                }
            )
        payload: dict[str, Any] = {"results": results}
        if unknown:
            payload["warning"] = f"unknown issuer(s) ignored: {unknown}"
        summary = f"{len(results)} chunks" + (f" for issuers={issuers}" if issuers else "")
        return ToolResult(payload=payload, summary=summary, latency_ms=0, retrieved=retrieved)

    def _lookup_facts(self, args: LookupFactsInput) -> ToolResult:
        metric = self.registry.resolve(args.metric)
        if metric is None:
            return ToolResult(
                payload={
                    "error": f"unknown metric {args.metric!r}",
                    "known_metrics": self.registry.describe(),
                },
                summary="unknown metric",
                latency_ms=0,
            )
        issuer = self.catalog.resolve_issuer(args.issuer) if args.issuer else None
        legal_entity = self._resolve_entity(args.legal_entity, issuer)
        if args.issuer and not issuer and not legal_entity:
            return ToolResult(
                payload={
                    "facts": [],
                    "error": f"no documents for issuer {args.issuer!r}",
                    "known_issuers": self.catalog.issuers(),
                    "coverage": self.catalog.coverage(),
                },
                summary=f"unknown issuer {args.issuer!r}",
                latency_ms=0,
            )
        facts = self.fact_store.lookup(
            issuer=issuer, legal_entity=legal_entity, metric=metric, years=args.fiscal_years
        )
        rows = [self._fact_row(f) for f in facts]
        for f in facts:
            self.seen_facts[f.fact_id] = f
        entities = (
            {
                e.legal_entity: {
                    "entity_type": e.entity_type,
                    "is_primary_issuer_report": e.is_primary_issuer_report,
                    "parent": e.parent_organisation,
                }
                for e in self.catalog.find(issuer=issuer)
                if e.legal_entity
            }
            if issuer
            else {}
        )
        payload = {
            "metric_resolved": metric,
            "facts": rows,
            "entities": entities,
            "coverage": self.fact_store.coverage(issuer=issuer, metric=metric),
            "document_coverage": self.catalog.coverage(issuer),
        }
        if not rows:
            payload["note"] = "no facts match; see coverage for what exists"
        years = sorted({f.fiscal_year for f in facts})
        return ToolResult(
            payload=payload,
            summary=f"{len(rows)} facts for {metric} years={years}",
            latency_ms=0,
            facts=rows,
        )

    def _compute_metric(self, args: ComputeMetricInput) -> ToolResult:
        inputs: dict[str, FactRecord] = {}
        missing: list[str] = []
        for name, fact_id in args.inputs.items():
            fact = self.seen_facts.get(fact_id)
            if fact is None:
                found = [
                    f
                    for f in self.fact_store.lookup(canonical_only=False, usable_only=False)
                    if f.fact_id == fact_id
                ]
                fact = found[0] if found else None
            (inputs.__setitem__(name, fact) if fact else missing.append(f"{name}={fact_id}"))
        if missing:
            return ToolResult(
                payload={
                    "error": "unknown fact ids (use fact_id values returned by lookup_facts)",
                    "missing": missing,
                },
                summary="unknown fact ids",
                latency_ms=0,
            )
        try:
            result = compute(self.registry, args.derived, inputs, args.params)
        except CurrencyMismatch as exc:
            return ToolResult(
                payload={
                    "error": "currency_mismatch",
                    "message": str(exc),
                    "currencies": exc.currencies,
                },
                summary="currency mismatch",
                latency_ms=0,
            )
        except ComputeError as exc:
            return ToolResult(payload={"error": str(exc)}, summary="compute error", latency_ms=0)
        payload = result.model_dump()
        payload["inputs"] = {name: self._fact_row(f) for name, f in inputs.items()}
        summary = (
            f"{args.derived} = {result.formatted}"
            if result.error is None
            else f"{args.derived}: {result.error}"
        )
        return ToolResult(
            payload=payload, summary=summary, latency_ms=0, facts=list(payload["inputs"].values())
        )

    def _final_answer(self, args: FinalAnswerInput) -> ToolResult:
        answer = self.build_answer(args)
        return ToolResult(payload=answer.model_dump(), summary="final answer", latency_ms=0)

    # ---------------------------------------------------------------- helpers

    def build_answer(self, args: FinalAnswerInput) -> AgentAnswer:
        """Enrich citations from the tool results seen in this run and drop unverifiable ones."""
        citations: list[Citation] = []
        dropped: list[str] = []
        for c in args.citations:
            citation = self._citation(c.kind, c.ref)
            (citations.append(citation) if citation else dropped.append(c.ref))
        caveats = list(args.caveats)
        if dropped:
            log.info("dropped unverifiable citations: %s", dropped)
        confidence: Confidence = (
            args.confidence if args.confidence in ("high", "medium", "low") else "medium"
        )  # type: ignore[assignment]
        if args.answered and not citations:
            caveats.append(
                "No citation could be verified against tool results; treat figures with caution."
            )
            confidence = "low"
        return AgentAnswer(
            resolved_question=args.resolved_question,
            answered=args.answered,
            answer_markdown=args.answer_markdown,
            citations=_dedupe_citations(citations),
            caveats=caveats,
            confidence=confidence,
            model_knowledge_note=args.model_knowledge_note,
            entities_used=args.entities_used,
            years_used=args.years_used,
            metrics_used=[self.registry.resolve(m) or m for m in args.metrics_used],
        )

    def _citation(self, kind: str, ref: str) -> Citation | None:
        if kind == "fact" and ref in self.seen_facts:
            f = self.seen_facts[ref]
            return Citation(
                document_id=f.document_id,
                file_name=f.file_name,
                legal_entity=f.legal_entity,
                fiscal_year=f.source_fiscal_year,
                page=f.page,
                kind="fact",
                ref=ref,
            )
        if kind == "chunk" and ref in self.seen_chunks:
            hit = self.seen_chunks[ref]
            m = hit.chunk.metadata
            return Citation(
                document_id=hit.chunk.document_id,
                file_name=str(m.get("file_name", "")),
                legal_entity=m.get("legal_entity") or None,
                fiscal_year=m.get("fiscal_year") or None,
                page=hit.chunk.page,
                kind="chunk",
                ref=ref,
            )
        # tolerate swapped kinds
        if ref in self.seen_facts:
            return self._citation("fact", ref)
        if ref in self.seen_chunks:
            return self._citation("chunk", ref)
        return None

    def _resolve_entity(self, name: str | None, issuer: str | None) -> str | None:
        if not name:
            return None
        needle = name.strip().lower()
        candidates = [d.legal_entity for d in self.catalog.find(issuer=issuer) if d.legal_entity]
        if not candidates:
            candidates = [e for ents in self.catalog.issuers().values() for e in ents]
        for entity in candidates:
            if entity.lower() == needle:
                return entity
        for entity in candidates:
            if needle in entity.lower() or entity.lower() in needle:
                return entity
        return None

    @staticmethod
    def _fact_row(f: FactRecord) -> dict[str, Any]:
        return {
            "fact_id": f.fact_id,
            "legal_entity": f.legal_entity,
            "metric": f.metric,
            "fiscal_year": f.fiscal_year,
            "value_reported": f.value_reported,
            "unit_reported": f.unit_reported,
            "currency": f.currency,
            "value_base": f.value_base,
            "formatted": f.formatted(),
            "page": f.page,
            "file_name": f.file_name,
            "source_fiscal_year": f.source_fiscal_year,
            "is_comparative": f.is_comparative,
            "confidence": f.confidence,
            "reconciliation_note": f.reconciliation_note,
            "label_seen": f.metric_alias_seen,
        }


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for c in citations:
        if c.ref not in seen:
            seen.add(c.ref)
            out.append(c)
    return out


def truncate_for_model(payload: dict[str, Any], max_chars: int) -> str:
    """JSON for the model, cut at ``max_chars`` with a marker (full payload lives in the trace)."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return (
        text[: max_chars - 60]
        + f'... [truncated; {len(text) - max_chars + 60} more chars in trace]"}}'
    )
