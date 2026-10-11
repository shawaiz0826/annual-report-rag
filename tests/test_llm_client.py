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
