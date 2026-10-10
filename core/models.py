"""Pydantic models — the contracts between layers.

Every object that crosses a boundary (loader → cleaner → chunker → index → agent →
service → UI/eval) is one of these. Nothing dataset-specific lives here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DocType = Literal["annual_report", "financial_statements", "news", "other"]
EntityType = Literal["group", "subsidiary", "other"]
ReportingUnit = Literal["units", "thousands", "millions", "billions"]
ChunkType = Literal["text", "table"]
Confidence = Literal["high", "medium", "low"]

UNIT_MULTIPLIER: dict[str, float] = {
    "units": 1.0,
    "thousands": 1e3,
    "millions": 1e6,
    "billions": 1e9,
}
"""Multiplier from a reported unit to base currency units."""


# --------------------------------------------------------------------------- ingestion


class PageText(BaseModel):
    """One page as returned by a loader: raw text plus any extracted tables."""

    page: int = Field(ge=1, description="1-based page number")
    text: str
    tables: list[list[list[str]]] = Field(default_factory=list)


class DocumentProfile(BaseModel):
    """What a document *is*, discovered at ingest time by the profiler."""

    document_id: str = Field(description="sha256 of file bytes, first 16 hex")
    source_path: str
    file_name: str
    doc_type: DocType
    issuer: str | None = Field(
        default=None, description="Canonical brand / group name, e.g. 'BMW'; None for news"
    )
    legal_entity: str | None = Field(
        default=None, description="e.g. 'BMW Finance N.V.', 'Tesla, Inc.'"
    )
    entity_type: EntityType | None = None
    parent_organisation: str | None = None
    is_primary_issuer_report: bool = Field(
        default=False, description="True only for the group/parent's own report"
    )
    fiscal_year: int | None = None
    period_end: str | None = Field(default=None, description="ISO date if stated")
    reporting_currency: str | None = Field(default=None, description="ISO 4217")
    reporting_unit: ReportingUnit | None = None
    sector: str | None = None
    country: str | None = None
    language: str = "en"
    page_count: int = 0
    summary: str = ""
    profiler_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    profiler_model: str = ""
    profiler_prompt_version: str = ""
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_financial(self) -> bool:
        """True for documents the facts extractor should process."""
        return self.doc_type in ("annual_report", "financial_statements")

    def flat_metadata(self) -> dict[str, Any]:
        """Flattened fields used as chunk metadata for filtering."""
        return {
            "document_id": self.document_id,
            "file_name": self.file_name,
            "issuer": self.issuer or "",
            "legal_entity": self.legal_entity or "",
            "entity_type": self.entity_type or "",
            "is_primary_issuer_report": self.is_primary_issuer_report,
            "fiscal_year": self.fiscal_year or 0,
            "doc_type": self.doc_type,
            "currency": self.reporting_currency or "",
            "sector": self.sector or "",
        }

    def provenance_header(self, page: int) -> str:
        """The header line prepended to every chunk of this document."""
        parts: list[str] = []
        if self.legal_entity:
            parts.append(self.legal_entity)
        doc_bits = [self.doc_type.replace("_", " ")]
        if self.fiscal_year:
            doc_bits.append(f"FY{self.fiscal_year}")
        parts.append(" ".join(doc_bits))
        money = " ".join(x for x in (self.reporting_currency, self.reporting_unit) if x)
        if money:
            parts.append(money)
        parts.append(f"p.{page}")
        return "[" + " | ".join(parts) + "]"


class Chunk(BaseModel):
    """An indexable unit of text with provenance."""

    chunk_id: str = Field(description="f'{document_id}:p{page}:c{n}' or ':t{n}' for tables")
    document_id: str
    page: int = Field(ge=1)
    chunk_type: ChunkType
    text: str = Field(description="Includes the provenance header line")
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str


class RetrievedChunk(BaseModel):
    """A chunk returned by hybrid retrieval with both ranks and the fused score."""

    chunk: Chunk
    dense_rank: int | None = None
    lexical_rank: int | None = None
    rrf_score: float
    expanded_text: str | None = Field(default=None, description="Page expansion if applied")

    @property
    def best_text(self) -> str:
        """The text to show the model: expanded page if available, else the chunk."""
        return self.expanded_text or self.chunk.text


# --------------------------------------------------------------------------- facts


class FactRecord(BaseModel):
    """One extracted financial figure with full provenance."""

    fact_id: str
    issuer: str
    legal_entity: str
    metric: str = Field(description="Canonical name from the registry")
    fiscal_year: int
    value_reported: float
    unit_reported: ReportingUnit
    currency: str
    value_base: float = Field(description="Value in base currency units")
    document_id: str
    file_name: str = ""
    page: int
    source_fiscal_year: int = Field(description="Fiscal year of the document it came from")
    is_comparative: bool = Field(description="fiscal_year != source_fiscal_year")
    metric_alias_seen: str = ""
    evidence_snippet: str = ""
    extraction_model: str = ""
    extraction_prompt_version: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reconciliation_note: str | None = None
    canonical: bool = True
    usable: bool = True
    verified: bool = Field(default=False, description="Set only by eval tooling")

    def formatted(self) -> str:
        """Human-readable rendering, e.g. 'EUR 98,282 millions'."""
        return f"{self.currency} {self.value_reported:,.0f} {self.unit_reported}"


class ComputeResult(BaseModel):
    """Result of a derived-metric computation."""

    derived: str
    value: float | None = None
    formatted: str = ""
    inputs_used: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    error: str | None = None
    missing: list[str] = Field(default_factory=list)
    available: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- agent


class Citation(BaseModel):
    """A pointer from an answer back to a page in a document."""

    document_id: str
    file_name: str
    legal_entity: str | None = None
    fiscal_year: int | None = None
    page: int
    kind: Literal["chunk", "fact"]
    ref: str = Field(description="chunk_id or fact_id")

    def label(self) -> str:
        """Inline citation label, e.g. '[BMW Finance N.V. FY2023 p.12]'."""
        bits = [
            b
            for b in (self.legal_entity, f"FY{self.fiscal_year}" if self.fiscal_year else None)
            if b
        ]
        bits.append(f"p.{self.page}")
        return "[" + " ".join(bits) + "]"


class AgentAnswer(BaseModel):
    """The structured final answer produced by the agent."""

    resolved_question: str = Field(description="The self-contained question that was answered")
    answered: bool = Field(description="False = not in documents / out of scope")
    answer_markdown: str
    citations: list[Citation] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"
    model_knowledge_note: str | None = None
    entities_used: list[str] = Field(default_factory=list)
    years_used: list[int] = Field(default_factory=list)
    metrics_used: list[str] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    """One tool invocation inside an agent run."""

    step: int
    tool: str
    arguments: dict[str, Any]
    result_summary: str
    result_ref: str | None = Field(default=None, description="Path of the full result in the trace")
    latency_ms: int


class Usage(BaseModel):
    """Token usage and cost for one request (summed over agent steps)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None

    def __add__(self, other: Usage) -> Usage:
        cost: float | None
        if self.cost_usd is None and other.cost_usd is None:
            cost = None
        else:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=cost,
        )


class Trace(BaseModel):
    """Everything that happened while answering one message."""

    trace_id: str
    session_id: str
    timestamp: str
    model: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    user_message: str
    history_turns: int = 0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    retrieved: list[dict[str, Any]] = Field(default_factory=list)
    facts_used: list[dict[str, Any]] = Field(default_factory=list)
    final: AgentAnswer | None = None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    error: str | None = None
    replayed: bool = False


# --------------------------------------------------------------------------- sessions


class Turn(BaseModel):
    """One message in a session."""

    role: Literal["user", "assistant"]
    content: str
    answer: AgentAnswer | None = None
    trace_id: str | None = None


class Session(BaseModel):
    """A conversation with slot memory for reference resolution."""

    session_id: str
    created_at: str
    turns: list[Turn] = Field(default_factory=list)
    slots: dict[str, Any] = Field(
        default_factory=dict, description="Last entities/years/metrics used"
    )


# --------------------------------------------------------------------------- llm


class LLMToolCall(BaseModel):
    """A tool call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """Normalised response from any OpenAI-compatible provider."""

    content: str | None
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str
    finish_reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
