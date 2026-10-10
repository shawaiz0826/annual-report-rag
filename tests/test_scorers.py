"""Eval scorers: numeric parsing with units/negatives/percent, behaviour, mentions, leaks."""

from core.models import AgentAnswer, Citation
from eval.scorers import behaviour_match, mentions, noise_leak, numeric_match, parse_numbers


def _answer(text: str, answered: bool = True, caveats: list[str] | None = None) -> AgentAnswer:
    return AgentAnswer(
        resolved_question="q", answered=answered, answer_markdown=text, caveats=caveats or []
    )


def test_parse_numbers_units_negatives_percent():
    found = parse_numbers(
        "Revenue was USD 96,773 million; loss of (1,981) million; 12.5%; $96.8 billion; 403,943 thousand; -394.3m"
    )
    values = [v for v, _, _ in found]
    assert (
        96_773e6 in values
        and -1_981e6 in values
        and 96.8e9 in values
        and 403_943e3 in values
        and -394.3e6 in values
    )
    assert any(abs(v - 0.125) < 1e-12 and pct for v, _, pct in found)


def test_numeric_match_bare_numbers_assume_millions_or_thousands():
    ok, missing = numeric_match(
        _answer("Tesla revenue FY2023: 96,773 (USD, millions)."),
        [{"value_base": 96773e6, "tolerance_pct": 0.5}],
    )
    assert ok and not missing
    ok, missing = numeric_match(
        _answer("about 96.8 billion"), [{"value_base": 96773e6, "tolerance_pct": 0.5}]
    )
    assert ok
    ok, missing = numeric_match(
        _answer("nothing numeric here"),
        [{"value_base": 96773e6, "metric": "revenue", "fiscal_year": 2023}],
    )
    assert not ok and missing == ["revenue FY2023=96773000000.0"]


def test_numeric_match_ratio_and_alternatives():
    ok, _ = numeric_match(
        _answer("net margin 15.5%"), [{"value_base": 0.1545, "kind": "ratio", "tolerance_pct": 2.0}]
    )
    assert ok
    ok, _ = numeric_match(
        _answer("net income 12,556 million"), [{"value_base": 12587e6, "alternatives": [12556e6]}]
    )
    assert ok


def test_behaviour_and_mentions_and_leak():
    assert behaviour_match(_answer("x"), "answer")
    assert behaviour_match(_answer("x", answered=False), "refuse")
    assert behaviour_match(_answer("x", caveats=["entity mismatch"]), "caveat")
    assert not behaviour_match(_answer("x"), "caveat")
    ok, problems = mentions(
        _answer("BMW Finance N.V. is a subsidiary", caveats=["latest group figure is FY2021"]),
        ["2021"],
        ["Finance N.V."],
        ["Toyota"],
    )
    assert ok and not problems
    ok, problems = mentions(_answer("plain"), ["2021"], ["Finance N.V."], ["plain"])
    assert not ok and len(problems) == 3
    a = _answer("x")
    a.citations = [Citation(document_id="d", file_name="news.pdf", page=1, kind="chunk", ref="c")]
    assert noise_leak(a, {"news.pdf": "news"}, ["news"])
    assert not noise_leak(a, {"news.pdf": "news"}, [])
