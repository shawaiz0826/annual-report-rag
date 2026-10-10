"""FastAPI service over ChatService. Run with `make api` (uvicorn api.main:app)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from core import __version__
from core.models import AgentAnswer, Trace, Usage
from core.service import ChatService

app = FastAPI(title="annual-report-rag", version=__version__)


@lru_cache(maxsize=1)
def service() -> ChatService:
    return ChatService()


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1)
    model: str | None = None
    show_model_knowledge: bool | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: AgentAnswer
    usage: Usage
    trace_id: str
    latency_ms: int
    trace: Trace


class IngestRequest(BaseModel):
    path: str | None = None
    force: bool = False


@app.get("/health")
def health() -> dict:
    svc = service()
    stats = svc.index_stats()
    return {
        "status": "ok",
        "model": svc.settings.llm_model,
        "fallback_model": svc.settings.llm_model_fallback,
        **stats,
    }


@app.post("/sessions")
def create_session() -> dict:
    return {"session_id": service().new_session().session_id}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    svc = service()
    session_id = req.session_id or svc.new_session().session_id
    answer, trace = svc.chat(
        session_id, req.message, model=req.model, show_model_knowledge=req.show_model_knowledge
    )
    return ChatResponse(
        session_id=trace.session_id,
        answer=answer,
        usage=trace.usage,
        trace_id=trace.trace_id,
        latency_ms=trace.latency_ms,
        trace=trace,
    )


@app.get("/catalog")
def catalog() -> dict:
    return service().catalog_view()


@app.get("/facts")
def facts(
    issuer: str | None = None,
    legal_entity: str | None = None,
    metric: str | None = None,
    years: str | None = None,
) -> dict:
    svc = service()
    resolved = svc.registry.resolve(metric) if metric else None
    if metric and not resolved:
        raise HTTPException(400, f"unknown metric {metric!r}; known: {svc.registry.names()}")
    year_list = [int(y) for y in years.split(",")] if years else None
    rows = svc.fact_store.lookup(
        issuer=svc.catalog.resolve_issuer(issuer) if issuer else None,
        legal_entity=legal_entity,
        metric=resolved,
        years=year_list,
    )
    return {
        "metric": resolved,
        "facts": [r.model_dump() for r in rows],
        "coverage": svc.fact_store.coverage(issuer),
    }


@app.get("/traces/latest")
def latest_trace() -> dict:
    svc = service()
    trace = svc.traces.latest
    if trace is None:
        raise HTTPException(404, "no trace yet")
    return {"trace": trace.model_dump(), "rendered": svc.explain(trace.trace_id)}


@app.get("/traces/{trace_id}")
def get_trace(trace_id: str) -> dict:
    svc = service()
    trace = svc.traces.get(trace_id)
    if trace is None:
        raise HTTPException(404, "trace not found")
    return {"trace": trace.model_dump(), "rendered": svc.explain(trace_id)}


@app.post("/ingest")
def ingest(req: IngestRequest) -> dict:
    """Synchronous for now; at scale this becomes a background job (see docs/ARCHITECTURE.md)."""
    report = service().ingest(Path(req.path) if req.path else None, force=req.force)
    return report.as_dict()
