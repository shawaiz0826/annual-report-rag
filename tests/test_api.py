"""FastAPI surface with an isolated, index-free service and a scripted provider."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from core.config import PathsConfig, SessionsConfig, Settings
from core.models import LLMResponse, LLMToolCall, Usage
from core.service import ChatService
from tests.test_agent import ScriptedProvider


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    settings = Settings(
        openrouter_api_key="test",
        paths=PathsConfig(
            raw_dir=tmp_path / "raw",
            derived_dir=tmp_path / "derived",
            index_dir=tmp_path / "index",
            traces_dir=tmp_path / "index" / "traces",
            replay_cache=tmp_path / "index" / "replay.json",
        ),
        sessions=SessionsConfig(backend="memory"),
    )
    provider = ScriptedProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="c1",
                        name="final_answer",
                        arguments={
                            "resolved_question": "q",
                            "answered": False,
                            "answer_markdown": "Not in the provided documents.",
                            "citations": [],
                            "confidence": "low",
                        },
                    )
                ],
                usage=Usage(prompt_tokens=5, completion_tokens=5, total_tokens=10, cost_usd=0.0),
                model="scripted/model",
            )
        ]
    )
    service = ChatService(settings=settings, provider=provider)
    monkeypatch.setattr(api_main, "service", lambda: service)
    return TestClient(api_main.app)


def test_health_catalog_sessions_chat_traces(client: TestClient):
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["documents"] == 0 and health["facts"] == 0

    assert client.get("/catalog").json() == {"issuers": {}, "other_documents": [], "coverage": {}}

    session_id = client.post("/sessions").json()["session_id"]
    body = client.post(
        "/chat", json={"session_id": session_id, "message": "Toyota revenue 2023?"}
    ).json()
    assert body["session_id"] == session_id
    assert body["answer"]["answered"] is False and body["usage"]["total_tokens"] == 10
    assert body["trace"]["tool_calls"][0]["tool"] == "final_answer"

    trace = client.get(f"/traces/{body['trace_id']}").json()
    assert "Toyota" in trace["rendered"]
    assert client.get("/traces/latest").json()["trace"]["trace_id"] == body["trace_id"]
    assert client.get("/traces/nope").status_code == 404

    assert client.get("/facts", params={"metric": "employees"}).status_code == 400
    assert client.get("/facts", params={"metric": "revenue"}).json()["facts"] == []
