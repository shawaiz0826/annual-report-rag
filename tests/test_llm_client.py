"""LLM client helpers: JSON repair/parsing and response normalisation (no network)."""

import json

import pytest

from core.llm.client import parse_json_object, repair_json, strip_code_fences


def test_repair_json_thousands_separators_outside_strings_only():
    raw = '{"items": [{"label": "Total revenues 96,773", "value": 96,773, "year": 2023,}],}'
    fixed = repair_json(raw)
    assert fixed == '{"items": [{"label": "Total revenues 96,773", "value": 96773, "year": 2023}]}'
    assert repair_json('{"a": [1,2,3], "b": 1,234,567}') == '{"a": [1,2,3], "b": 1234567}'
    assert repair_json('{"a": "x\\"y,1,234"}') == '{"a": "x\\"y,1,234"}'


def test_parse_json_object_with_fences_prose_and_slips():
    text = 'Here you go:\n```json\n{"items": [{"value": 96,773, "fiscal_year": 2023}]}\n```'
    assert parse_json_object(text) == {"items": [{"value": 96773, "fiscal_year": 2023}]}
    assert strip_code_fences("```json\n{}\n```") == "{}"
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")
    with pytest.raises(json.JSONDecodeError):
        parse_json_object("no json here")


def test_quota_error_description_handles_unwrapped_body():
    """OpenRouter 429 bodies arrive as the inner error dict; describe them cleanly."""
    import httpx
    from openai import APIStatusError

    from core.llm.client import OpenAICompatibleProvider, _is_quota_error

    body = {
        "message": "Rate limit exceeded: free-models-per-day. Add 10 credits",
        "code": 429,
        "metadata": {
            "headers": {"X-RateLimit-Reset": "1791763200000"},
            "limit_source": "openrouter_free_tier_daily",
        },
    }
    response = httpx.Response(429, request=httpx.Request("POST", "https://x"))
    exc = APIStatusError("Error code: 429 - {...}", response=response, body=body)
    assert _is_quota_error(exc)
    text = OpenAICompatibleProvider._describe(exc)
    assert text.startswith("HTTP 429: Rate limit exceeded: free-models-per-day")
    assert "resets 2026-10-12 00:00 UTC" in text
    wrapped = APIStatusError("x", response=response, body={"error": body})
    assert _is_quota_error(wrapped)
    assert OpenAICompatibleProvider._describe(wrapped) == text


class _Completions:
    """Stub for `client.chat.completions` that raises or answers per model id."""

    def __init__(self, behaviour: dict[str, object]) -> None:
        self.behaviour = behaviour
        self.calls: list[str] = []

    def create(self, **kwargs):
        model = kwargs["model"]
        self.calls.append(model)
        outcome = self.behaviour[model]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _provider(behaviour, *, fallback=True, retries=0):
    from types import SimpleNamespace

    from core.config import Settings
    from core.llm.client import OpenAICompatibleProvider

    settings = Settings(
        openrouter_api_key="k",
        llm_model="primary/model",
        llm_model_fallback="fallback/model",
        llm={"model_fallback": fallback, "max_retries": retries, "backoff_base_s": 0.0},
    )
    provider = OpenAICompatibleProvider(settings)
    completions = _Completions(behaviour)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


def _quota_error():
    import httpx
    from openai import APIStatusError

    response = httpx.Response(429, request=httpx.Request("POST", "https://x"))
    return APIStatusError(
        "Error code: 429",
        response=response,
        body={"message": "Rate limit exceeded: free-models-per-day", "code": 429},
    )


def _ok(model: str) -> dict:
    return {
        "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0},
        "model": model,
    }


def test_chat_falls_back_to_second_model_and_records_it():
    provider, completions = _provider(
        {"primary/model": _quota_error(), "fallback/model": _ok("fallback/model")}
    )
    response = provider.chat([{"role": "user", "content": "hi"}])
    assert completions.calls == ["primary/model", "fallback/model"]
    assert response.model == "fallback/model"
    assert response.fallback_from == "primary/model"
    assert "free-models-per-day" in (response.fallback_reason or "")


def test_chat_without_fallback_raises_quota_error():
    from core.llm.client import QuotaExhaustedError

    provider, completions = _provider({"primary/model": _quota_error()}, fallback=False)
    with pytest.raises(QuotaExhaustedError):
        provider.chat([{"role": "user", "content": "hi"}])
    assert completions.calls == ["primary/model"]


def test_chat_prefers_quota_error_when_whole_chain_fails():
    import httpx
    from openai import APIStatusError

    from core.llm.client import QuotaExhaustedError

    not_found = APIStatusError(
        "Error code: 404",
        response=httpx.Response(404, request=httpx.Request("POST", "https://x")),
        body={"message": "No endpoints found", "code": 404},
    )
    provider, completions = _provider(
        {"primary/model": _quota_error(), "fallback/model": not_found}
    )
    with pytest.raises(QuotaExhaustedError):
        provider.chat([{"role": "user", "content": "hi"}])
    assert completions.calls == ["primary/model", "fallback/model"]


def test_explicit_model_equal_to_fallback_is_tried_once():
    provider, completions = _provider({"fallback/model": _ok("fallback/model")})
    response = provider.chat([{"role": "user", "content": "hi"}], model="fallback/model")
    assert completions.calls == ["fallback/model"]
    assert response.fallback_from is None
