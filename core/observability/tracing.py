"""Tracing: one :class:`~core.models.Trace` per request, JSONL on disk, full tool results beside it."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.models import AgentAnswer, Trace

log = logging.getLogger(__name__)


def new_trace_id() -> str:
    """Short random id."""
    return uuid.uuid4().hex[:12]


class TraceStore:
    """Writes traces to ``<traces_dir>/YYYY-MM-DD.jsonl`` and tool results to ``results/<trace>/<step>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "results").mkdir(exist_ok=True)
        self.latest: Trace | None = None

    def write(self, trace: Trace) -> None:
        """Append a trace to today's JSONL file."""
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        with (self.root / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(trace.model_dump(mode="json"), ensure_ascii=False) + "\n")
        self.latest = trace

    def write_result(self, trace_id: str, step: int, payload: Any) -> str:
        """Store a full tool result; returns its relative path (used as ``result_ref``)."""
        folder = self.root / "results" / trace_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{step}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        return str(path.relative_to(self.root))

    def read_result(self, result_ref: str) -> Any:
        """Load a full tool result by its ``result_ref``."""
        return json.loads((self.root / result_ref).read_text(encoding="utf-8"))

    def get(self, trace_id: str) -> Trace | None:
        """Find a trace by id (latest in memory first, then the JSONL files, newest first)."""
        if self.latest is not None and self.latest.trace_id == trace_id:
            return self.latest
        for path in sorted(self.root.glob("*.jsonl"), reverse=True):
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    if f'"trace_id": "{trace_id}"' in line:
                        return Trace.model_validate_json(line)
        return None


def render_trace(trace: Trace) -> str:
    """Human-readable explanation of a trace (the `/why` view)."""
    lines = [
        f"trace {trace.trace_id} · session {trace.session_id} · model {trace.model}"
        + (" · [replay]" if trace.replayed else "")
    ]
    lines.append(f"prompts: {trace.prompt_versions}")
    lines.append(f"question: {trace.user_message}")
    final: AgentAnswer | None = trace.final
    if final:
        lines.append(f"resolved: {final.resolved_question}")
        lines.append(
            f"answered={final.answered} confidence={final.confidence} entities={final.entities_used} years={final.years_used} metrics={final.metrics_used}"
        )
    lines.append("")
    lines.append("tool calls:")
    for call in trace.tool_calls:
        lines.append(
            f"  {call.step}. {call.tool}({json.dumps(call.arguments, ensure_ascii=False)[:160]}) → {call.result_summary[:160]} [{call.latency_ms} ms]"
        )
    if trace.retrieved:
        lines.append("")
        lines.append("retrieved chunks (rrf · dense rank · bm25 rank):")
        for r in trace.retrieved[:20]:
            lines.append(
                f"  {r.get('rrf_score', 0):.4f} · d{r.get('dense_rank') or '-'} · b{r.get('lexical_rank') or '-'}  "
                f"{r.get('legal_entity') or r.get('doc_type')} FY{r.get('fiscal_year')} p.{r.get('page')}  [{r.get('chunk_id')}]"
            )
    if trace.facts_used:
        lines.append("")
        lines.append("facts used:")
        for f in trace.facts_used[:20]:
            lines.append(
                f"  {f.get('legal_entity')} {f.get('metric')} FY{f.get('fiscal_year')} = {f.get('currency')} {f.get('value_reported'):,} {f.get('unit_reported')} "
                f"(p.{f.get('page')}, conf {f.get('confidence')}, {f.get('reconciliation_note') or 'no note'})"
            )
    if final and final.caveats:
        lines.append("")
        lines.append("caveats: " + " | ".join(final.caveats))
    u = trace.usage
    cost = f"${u.cost_usd:.4f}" if u.cost_usd is not None else "n/a"
    lines.append("")
    lines.append(
        f"usage: {u.prompt_tokens} in / {u.completion_tokens} out · cost {cost} · {trace.latency_ms} ms"
    )
    if trace.error:
        lines.append(f"error: {trace.error}")
    return "\n".join(lines)
