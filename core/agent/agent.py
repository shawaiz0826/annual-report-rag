"""The agent loop: tool-calling LLM over the toolbox, producing an AgentAnswer and a Trace."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from core.agent.prompts import load_prompt, prompt_versions
from core.agent.tools import FinalAnswerInput, ToolBox, tool_schemas, truncate_for_model
from core.config import AgentConfig
from core.index.catalog import Catalog
from core.llm.client import LLMError, LLMProvider, QuotaExhaustedError
from core.models import AgentAnswer, Session, ToolCallRecord, Trace, Usage
from core.observability.tracing import TraceStore, new_trace_id

log = logging.getLogger(__name__)

MODEL_KNOWLEDGE_ON = (
    "Model knowledge is ENABLED for this session: after the grounded answer you may add a clearly "
    'separate `model_knowledge_note` beginning with "Outside the provided documents (unverified): " — '
    "never mix it into `answer_markdown`."
)
MODEL_KNOWLEDGE_OFF = (
    "Model knowledge is DISABLED: never use prior knowledge; leave `model_knowledge_note` null."
)


def catalog_summary(catalog: Catalog) -> str:
    """Compact, generated description of what is indexed (no dataset knowledge in code)."""
    lines: list[str] = []
    for issuer, entities in catalog.issuers().items():
        parts = []
        for entity in entities:
            docs = [d for d in catalog.find(issuer=issuer, legal_entity=entity) if d.is_financial]
            if not docs:
                continue
            years = sorted({d.fiscal_year for d in docs if d.fiscal_year})
            kind = docs[0].entity_type or "entity"
            ccy = docs[0].reporting_currency or "?"
            unit = docs[0].reporting_unit or "?"
            flag = (
                " (group report)"
                if kind == "group"
                else f" ({kind} of {docs[0].parent_organisation or issuer})"
            )
            parts.append(f"{entity}{flag}: FY{', FY'.join(map(str, years))}, {ccy} {unit}")
        lines.append(f"- {issuer}: " + "; ".join(parts))
    other = [d for d in catalog.documents() if not d.is_financial]
    if other:
        lines.append(
            "- Non-financial documents: "
            + ", ".join(f"{d.file_name} ({d.doc_type})" for d in other)
        )
    return "\n".join(lines) or "- (no documents ingested yet)"


class Agent:
    """Runs one question through the tool loop."""

    def __init__(
        self,
        provider: LLMProvider,
        toolbox_factory: Any,
        catalog: Catalog,
        config: AgentConfig,
        traces: TraceStore,
    ) -> None:
        self.provider = provider
        self.toolbox_factory = toolbox_factory
        self.catalog = catalog
        self.config = config
        self.traces = traces

    # ------------------------------------------------------------------ prompt assembly

    def system_prompt(self, show_model_knowledge: bool) -> str:
        """System prompt with the generated catalog summary and the model-knowledge flag."""
        _, body = load_prompt("system")
        return body.replace("{{catalog_summary}}", catalog_summary(self.catalog)).replace(
            "{{model_knowledge_line}}",
            MODEL_KNOWLEDGE_ON if show_model_knowledge else MODEL_KNOWLEDGE_OFF,
        )

    def history_messages(self, session: Session) -> list[dict[str, Any]]:
        """Condensed recent turns plus a context-slots line."""
        turns = session.turns[-2 * self.config.max_history_turns :]
        messages: list[dict[str, Any]] = []
        for turn in turns:
            if turn.role == "user":
                messages.append({"role": "user", "content": turn.content})
            else:
                text = turn.answer.answer_markdown if turn.answer else turn.content
                text = text[: self.config.history_answer_chars]
                if turn.answer and turn.answer.caveats:
                    text += "\n(caveats: " + "; ".join(turn.answer.caveats)[:200] + ")"
                messages.append({"role": "assistant", "content": text})
        slots = session.slots
        if slots:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "(Conversation context — the previous answer was about entities="
                        f"{slots.get('entities')}, years={slots.get('years')}, metrics={slots.get('metrics')}. "
                        "Resolve references in the next question against this; it is not a new question.)"
                    ),
                }
            )
        return messages

    # ------------------------------------------------------------------ run

    def run(
        self,
        session: Session,
        message: str,
        *,
        model: str | None = None,
        show_model_knowledge: bool = False,
    ) -> tuple[AgentAnswer, Trace]:
        """Answer ``message`` in the context of ``session``."""
        started = time.perf_counter()
        trace = Trace(
            trace_id=new_trace_id(),
            session_id=session.session_id,
            timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
            model=model or self.provider.default_model,
            prompt_versions=prompt_versions(),
            user_message=message,
            history_turns=len(session.turns),
        )
        toolbox: ToolBox = self.toolbox_factory()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt(show_model_knowledge)},
            *self.history_messages(session),
            {"role": "user", "content": message},
        ]
        schemas = tool_schemas()
        usage = Usage()
        answer: AgentAnswer | None = None
        step = 0
        facts_seen: dict[str, dict[str, Any]] = {}
        retrieved_seen: dict[str, dict[str, Any]] = {}

        def record_tool(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            nonlocal step
            step += 1
            result = toolbox.run(name, arguments)
            ref = self.traces.write_result(
                trace.trace_id,
                step,
                {"tool": name, "arguments": arguments, "result": result.payload},
            )
            trace.tool_calls.append(
                ToolCallRecord(
                    step=step,
                    tool=name,
                    arguments=arguments,
                    result_summary=result.summary,
                    result_ref=ref,
                    latency_ms=result.latency_ms,
                )
            )
            for r in result.retrieved:
                retrieved_seen.setdefault(r["chunk_id"], r)
            for f in result.facts:
                facts_seen.setdefault(f["fact_id"], f)
            return truncate_for_model(
                result.payload, self.config.tool_result_max_chars
            ), result.payload

        try:
            for iteration in range(self.config.max_steps + 1):
                force_final = iteration == self.config.max_steps
                response = self.provider.chat(
                    messages,
                    tools=schemas,
                    tool_choice={"type": "function", "function": {"name": "final_answer"}}
                    if force_final
                    else "auto",
                    temperature=self.config.temperature,
                    model=model,
                    max_tokens=self.config.max_output_tokens,
                )
                usage = usage + response.usage
                if response.fallback_from and trace.fallback_from is None:
                    trace.fallback_from = response.fallback_from
                    trace.fallback_reason = response.fallback_reason
                    trace.model = response.model
                if not response.tool_calls:
                    # Plain text is not an answer; nudge once per iteration.
                    messages.append({"role": "assistant", "content": response.content or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": "You must use tools. Call `final_answer` to finish, with citations.",
                        }
                    )
                    continue
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": response.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in response.tool_calls
                    ],
                }
                messages.append(assistant_msg)
                final_call = None
                for call in response.tool_calls:
                    if call.name == "final_answer":
                        final_call = call
                        continue
                    text, _ = record_tool(call.name, call.arguments)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
                if final_call is not None:
                    try:
                        args = FinalAnswerInput.model_validate(final_call.arguments)
                    except Exception as exc:
                        step += 1
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": final_call.id,
                                "content": f"final_answer rejected: {str(exc)[:400]}. Fix the arguments and call final_answer again.",
                            }
                        )
                        continue
                    answer = toolbox.build_answer(args)
                    step += 1
                    trace.tool_calls.append(
                        ToolCallRecord(
                            step=step,
                            tool="final_answer",
                            arguments=final_call.arguments,
                            result_summary="final answer",
                            result_ref=None,
                            latency_ms=0,
                        )
                    )
                    break
        except QuotaExhaustedError as exc:
            trace.error = str(exc)
            log.error("agent stopped: model quota exhausted: %s", exc)
            answer = AgentAnswer(
                resolved_question=message,
                answered=False,
                answer_markdown=(
                    "No answer was generated: the model quota is exhausted "
                    f"({exc}). The documents were not consulted, so this is not a "
                    "statement about their contents."
                ),
                confidence="low",
                caveats=[
                    "model quota exhausted — wait for the reset, add credits, or switch model"
                ],
            )
        except LLMError as exc:
            trace.error = str(exc)
            log.error("agent failed: %s", exc)

        if answer is None:
            answer = AgentAnswer(
                resolved_question=message,
                answered=False,
                answer_markdown=(
                    "I could not produce a grounded answer: "
                    + (trace.error or "the step limit was reached without a final answer.")
                ),
                confidence="low",
                caveats=["agent did not finish normally"],
            )
        if not show_model_knowledge:
            answer.model_knowledge_note = None
        if trace.fallback_from:
            answer.caveats.append(
                f"answered by fallback model {trace.model}: {trace.fallback_from} failed "
                f"({trace.fallback_reason})"
            )

        trace.final = answer
        trace.usage = usage
        trace.latency_ms = int((time.perf_counter() - started) * 1000)
        trace.retrieved = list(retrieved_seen.values())
        cited = {c.ref for c in answer.citations}
        trace.facts_used = [f for f in facts_seen.values() if f["fact_id"] in cited] or list(
            facts_seen.values()
        )[:20]
        self.traces.write(trace)
        return answer, trace
