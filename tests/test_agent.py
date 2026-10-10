"""Agent loop end-to-end with a scripted LLM provider (no network, no models, no dataset)."""

from __future__ import annotations

import json
from pathlib import Path

from core.agent.agent import Agent, catalog_summary
from core.agent.memory import InMemorySessionStore, slots_from_answer
from core.agent.tools import ToolBox, tool_schemas
from core.config import AgentConfig
from core.facts.registry import load_registry
from core.facts.store import FactStore
from core.index.catalog import Catalog
from core.models import (
    AgentAnswer,
    Chunk,
    DocumentProfile,
    FactRecord,
    LLMResponse,
    LLMToolCall,
    RetrievedChunk,
    Turn,
    Usage,
)
from core.observability.tracing import TraceStore, render_trace


class ScriptedProvider:
    """Returns pre-scripted responses in order; records the messages it was sent."""

    default_model = "scripted/model"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages, **kwargs):
        self.calls.append([dict(m) for m in messages])  # snapshot: the agent mutates the list
        return self.responses.pop(0)


class FakeRetriever:
    def __init__(self, chunk: Chunk) -> None:
        self.chunk = chunk
        self.queries: list[str] = []

    def search(self, query, filters=None, k=None, issuers=None):
        self.queries.append(query)
        return [RetrievedChunk(chunk=self.chunk, dense_rank=1, lexical_rank=2, rrf_score=0.03)]


def _profile() -> DocumentProfile:
    return DocumentProfile(
        document_id="docA",
        source_path="a.pdf",
        file_name="Acme_2023.pdf",
        doc_type="annual_report",
        issuer="Acme",
        legal_entity="Acme plc",
        entity_type="group",
        is_primary_issuer_report=True,
        fiscal_year=2023,
        reporting_currency="EUR",
        reporting_unit="millions",
        page_count=10,
    )


def _fact(fid: str, metric: str, year: int, value: float) -> FactRecord:
    return FactRecord(
        fact_id=fid,
        issuer="Acme",
        legal_entity="Acme plc",
        metric=metric,
        fiscal_year=year,
        value_reported=value,
        unit_reported="millions",
        currency="EUR",
        value_base=value * 1e6,
        document_id="docA",
        file_name="Acme_2023.pdf",
        page=7,
        source_fiscal_year=2023,
        is_comparative=year != 2023,
        confidence=0.8,
    )


def _call(cid: str, name: str, **arguments) -> LLMToolCall:
    return LLMToolCall(id=cid, name=name, arguments=arguments)


def _resp(*calls: LLMToolCall, content: str | None = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=list(calls),
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.0),
        model="scripted/model",
    )


def _build(tmp_path: Path, provider: ScriptedProvider):
    catalog = Catalog(tmp_path / "catalog.json")
    catalog.add(_profile())
    store = FactStore(tmp_path / "facts.db")
    store.replace_all(
        [
            _fact("f_rev23", "revenue", 2023, 1000),
            _fact("f_ni23", "net_income", 2023, 150),
            _fact("f_rev22", "revenue", 2022, 800),
        ]
    )
    chunk = Chunk(
        chunk_id="docA:p3:c0",
        document_id="docA",
        page=3,
        chunk_type="text",
        text="[Acme plc] outlook text",
        metadata=_profile().flat_metadata() | {"page": 3},
        content_hash="h",
    )
    retriever = FakeRetriever(chunk)
    registry = load_registry()
    traces = TraceStore(tmp_path / "traces")
    agent = Agent(
        provider,
        lambda: ToolBox(catalog, retriever, store, registry),
        catalog,
        AgentConfig(max_steps=4),
        traces,
    )
    return agent, retriever, traces


def test_tool_schemas_are_valid_function_definitions():
    schemas = tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {
        "list_catalog",
        "search_documents",
        "lookup_facts",
        "compute_metric",
        "final_answer",
    }
    assert all(s["function"]["parameters"]["type"] == "object" for s in schemas)


def test_catalog_summary_is_generated_from_profiles(tmp_path: Path):
    catalog = Catalog(tmp_path / "c.json")
    catalog.add(_profile())
    text = catalog_summary(catalog)
    assert "Acme plc (group report): FY2023, EUR millions" in text


def test_end_to_end_lookup_compute_final(tmp_path: Path):
    provider = ScriptedProvider(
        [
            _resp(
                _call("c1", "lookup_facts", issuer="Acme", metric="revenue"),
                _call(
                    "c2", "lookup_facts", issuer="acme", metric="net income", fiscal_years=[2023]
                ),
            ),
            _resp(
                _call(
                    "c3",
                    "compute_metric",
                    derived="net_margin",
                    inputs={"net_income": "f_ni23", "revenue": "f_rev23"},
                )
            ),
            _resp(
                _call(
                    "c4",
                    "final_answer",
                    resolved_question="Acme net margin 2023?",
                    answered=True,
                    answer_markdown="Net margin was 15.0% [Acme plc FY2023 p.7].",
                    citations=[
                        {"kind": "fact", "ref": "f_rev23"},
                        {"kind": "fact", "ref": "f_ni23"},
                        {"kind": "chunk", "ref": "bogus"},
                    ],
                    confidence="high",
                    entities_used=["Acme plc"],
                    years_used=[2023],
                    metrics_used=["net income", "revenue"],
                )
            ),
        ]
    )
    agent, _retriever, traces = _build(tmp_path, provider)
    store = InMemorySessionStore()
    session = store.create()
    answer, trace = agent.run(session, "What was Acme's net margin in 2023?")

    assert answer.answered and answer.confidence == "high"
    assert [c.ref for c in answer.citations] == ["f_rev23", "f_ni23"]  # bogus dropped, order kept
    assert answer.citations[0].page == 7 and answer.citations[0].legal_entity == "Acme plc"
    assert answer.metrics_used == ["net_income", "revenue"]
    assert [t.tool for t in trace.tool_calls] == [
        "lookup_facts",
        "lookup_facts",
        "compute_metric",
        "final_answer",
    ]
    assert trace.tool_calls[2].result_summary == "net_margin = 15.0%"
    assert trace.usage.total_tokens == 45 and trace.prompt_versions["system"]
    assert {f["fact_id"] for f in trace.facts_used} == {"f_rev23", "f_ni23"}
    # tool results were stored in full and are readable
    full = traces.read_result(trace.tool_calls[0].result_ref)
    assert full["result"]["metric_resolved"] == "revenue" and len(full["result"]["facts"]) == 2
    # the model saw the catalog in its system prompt and tool messages in order
    first_messages = provider.calls[0]
    assert "Acme plc (group report)" in first_messages[0]["content"]
    assert provider.calls[1][-1]["role"] == "tool"
    assert "net margin" in render_trace(trace).lower()
    # slots for follow-ups
    slots = slots_from_answer(answer, session.slots)
    assert slots["entities"] == ["Acme plc"] and slots["years"] == [2023]


def test_followup_gets_history_and_context_slots(tmp_path: Path):
    provider = ScriptedProvider(
        [
            _resp(
                _call(
                    "c1",
                    "final_answer",
                    resolved_question="Acme revenue 2022?",
                    answered=True,
                    answer_markdown="EUR 800 million",
                    citations=[],
                    confidence="medium",
                )
            ),
        ]
    )
    agent, _r, _t = _build(tmp_path, provider)
    store = InMemorySessionStore()
    session = store.create()
    previous = AgentAnswer(
        resolved_question="Acme revenue 2023?",
        answered=True,
        answer_markdown="EUR 1,000 million",
        entities_used=["Acme plc"],
        years_used=[2023],
        metrics_used=["revenue"],
    )
    store.append(session.session_id, Turn(role="user", content="Acme revenue 2023?"))
    store.append(
        session.session_id,
        Turn(role="assistant", content=previous.answer_markdown, answer=previous),
    )
    store.update_slots(session.session_id, slots_from_answer(previous))
    answer, _trace = agent.run(store.get(session.session_id), "And 2022?")
    sent = provider.calls[0]
    roles = [m["role"] for m in sent]
    assert roles == ["system", "user", "assistant", "user", "user"]
    assert "entities=['Acme plc']" in sent[3]["content"] and sent[4]["content"] == "And 2022?"
    assert answer.caveats and "No citation" in answer.caveats[0] and answer.confidence == "low"


def test_plain_text_is_nudged_then_forced_to_final(tmp_path: Path):
    provider = ScriptedProvider(
        [
            _resp(content="Revenue was 1000."),
            _resp(
                _call(
                    "c1",
                    "final_answer",
                    resolved_question="q",
                    answered=False,
                    answer_markdown="Not in documents.",
                    citations=[],
                    confidence="low",
                )
            ),
        ]
    )
    agent, _r, _t = _build(tmp_path, provider)
    answer, trace = agent.run(InMemorySessionStore().create(), "q")
    assert not answer.answered and trace.error is None
    assert provider.calls[1][-1]["content"].startswith("You must use tools")


def test_currency_mismatch_surfaces_as_tool_error(tmp_path: Path):
    provider = ScriptedProvider([_resp(_call("c0", "lookup_facts", metric="revenue"))])
    agent, _r, _t = _build(tmp_path, provider)
    toolbox = agent.toolbox_factory()
    toolbox.run("lookup_facts", {"metric": "revenue"})
    usd = _fact("f_usd", "revenue", 2023, 500).model_copy(
        update={"currency": "USD", "legal_entity": "Other Inc"}
    )
    toolbox.seen_facts["f_usd"] = usd
    result = toolbox.run(
        "compute_metric", {"derived": "difference", "inputs": {"a": "f_rev23", "b": "f_usd"}}
    )
    assert (
        result.payload["error"] == "currency_mismatch"
        and "different currencies" in result.payload["message"]
    )
    unknown = toolbox.run(
        "compute_metric", {"derived": "ratio", "inputs": {"a": "nope", "b": "f_rev23"}}
    )
    assert "unknown fact ids" in unknown.payload["error"]
    bad_metric = toolbox.run("lookup_facts", {"metric": "employees"})
    assert "unknown metric" in bad_metric.payload["error"]
    bad_issuer = toolbox.run("lookup_facts", {"metric": "revenue", "issuer": "Zeta Motors"})
    assert (
        "no documents for issuer" in bad_issuer.payload["error"] and bad_issuer.payload["coverage"]
    )
    assert json.dumps(bad_issuer.payload)  # serialisable
