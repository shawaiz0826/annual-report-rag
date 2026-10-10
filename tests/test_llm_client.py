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
