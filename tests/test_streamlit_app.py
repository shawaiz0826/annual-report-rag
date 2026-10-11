"""The web chat, driven headlessly with Streamlit's AppTest over an index-free service."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from core.config import PathsConfig, SessionsConfig, Settings
from core.models import LLMResponse, LLMToolCall, Usage
from core.service import ChatService
from tests.test_agent import ScriptedProvider, _fact, _profile

APP = str(Path(__file__).resolve().parents[1] / "apps" / "streamlit_app.py")


def _responses() -> list[LLMResponse]:
    usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.0)
    lookup = LLMResponse(
        content=None,
        tool_calls=[
            LLMToolCall(
                id="c1",
                name="lookup_facts",
                arguments={"issuer": "Acme", "metric": "revenue", "fiscal_years": [2023]},
            )
        ],
        usage=usage,
        model="scripted/model",
    )
    final = LLMResponse(
        content=None,
        tool_calls=[
            LLMToolCall(
                id="c2",
                name="final_answer",
                arguments={
                    "resolved_question": "Acme revenue 2023?",
                    "answered": True,
                    "answer_markdown": "Revenue was EUR 1,000 million [Acme plc FY2023 p.7].",
                    "citations": [{"kind": "fact", "ref": "f_rev23"}],
                    "confidence": "high",
                    "entities_used": ["Acme plc"],
                    "years_used": [2023],
                    "metrics_used": ["revenue"],
                },
            )
        ],
        usage=usage,
        model="fallback/model",
        fallback_from="scripted/model",
        fallback_reason="HTTP 503: upstream unavailable",
    )
    return [lookup, final]


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    settings = Settings(
        openrouter_api_key="test",
        paths=PathsConfig(
            raw_dir=tmp_path / "raw",
            derived_dir=tmp_path / "derived",
            index_dir=tmp_path / "index",
            traces_dir=tmp_path / "index" / "traces",
        ),
        sessions=SessionsConfig(backend="memory"),
    )
    service = ChatService(settings=settings, provider=ScriptedProvider(_responses()))
    service.catalog.add(_profile())
    service.catalog.set_stats("docA", chunks=12, facts=3)
    service.catalog.save()
    service.fact_store.replace_all(
        [_fact("f_rev23", "revenue", 2023, 1000), _fact("f_rev22", "revenue", 2022, 800)]
    )
    monkeypatch.setattr("core.service.ChatService", lambda: service)
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)


def test_chat_round_trip_renders_answer_sources_and_explanation(app: AppTest):
    assert "Acme plc: 2023" in _text(app)  # coverage in the sidebar
    app.chat_input[0].set_value("What was Acme's revenue in 2023?").run()
    assert not app.exception, [e.value for e in app.exception]

    text = _text(app)
    assert "Revenue was EUR 1,000 million" in text
    assert "Acme plc FY2023 p.7" in text and "Acme_2023.pdf" in text  # sources line
    assert "confidence high" in text and "fallback/model" in text
    assert any("fallback model fallback/model" in w.value for w in app.warning)  # caveat
    assert any("upstream unavailable" in w.value for w in app.warning)  # explanation panel
    assert "lookup_facts" in text and "final_answer" in text  # tool-call list
    assert len(app.dataframe) >= 1  # facts used table
    assert "trace " in text and "tool calls:" in "".join(c.value for c in app.code)


def test_catalog_and_facts_pages(app: AppTest):
    app.radio[0].set_value("Catalog").run()
    assert not app.exception
    assert len(app.dataframe) == 1
    assert "Acme plc: 2023" in _text(app)

    app.radio[0].set_value("Facts").run()
    assert not app.exception
    assert len(app.dataframe) == 1
    assert "2 facts" in _text(app)
