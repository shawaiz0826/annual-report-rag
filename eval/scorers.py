"""Scorers for the golden set. Pure functions over (AgentAnswer, Trace, expectation)."""

from __future__ import annotations

import re
from typing import Any

from core.models import AgentAnswer, Trace

_UNIT = {
    "thousand": 1e3, "thousands": 1e3, "k": 1e3,
    "million": 1e6, "millions": 1e6, "mn": 1e6, "m": 1e6, "mm": 1e6,
    "billion": 1e9, "billions": 1e9, "bn": 1e9, "b": 1e9,
}  # fmt: skip

_NUMBER = re.compile(
    r"(?P<neg>[-−–‐‑‒—(]\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<close>\))?"
    r"\s*(?P<unit>thousands?|millions?|billions?|bn|mn|mm|[kmb](?![a-z]))?"
    r"\s*(?P<pct>%|percent)?",
    re.IGNORECASE,
)


_LOSS_WORDS = re.compile(r"\b(loss|losses|deficit|negative)\b", re.IGNORECASE)


def parse_numbers(text: str) -> list[tuple[float, bool, bool]]:
    """Return ``[(value_in_base_units, had_unit_word, is_percent), ...]`` found in ``text``.

    Thousands separators and parentheses/minus signs (any dash variant) are handled;
    ``million``/``bn``-style unit words scale the value; percentages are returned as
    fractions. A number preceded within 40 characters by "loss"/"deficit" is also
    emitted as its negative, so "a net loss of USD 1,981 million" matches −1,981.
    """
    out: list[tuple[float, bool, bool]] = []
    for m in _NUMBER.finditer(text):
        raw = m.group("num").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        negative = bool(m.group("neg")) and (
            m.group("close") is not None or not m.group("neg").strip().startswith("(")
        )
        loss_context = bool(_LOSS_WORDS.search(text[max(0, m.start() - 40) : m.start()]))
        if negative:
            value = -value
        if m.group("pct"):
            out.append((value / 100.0, False, True))
            continue
        unit = (m.group("unit") or "").lower()
        scaled = value * _UNIT[unit] if unit else value
        out.append((scaled, bool(unit), False))
        if loss_context and value > 0:
            out.append((-scaled, bool(unit), False))
    return out


def _close(a: float, b: float, tolerance_pct: float) -> bool:
    if a == b:
        return True
    return abs(a - b) <= abs(b) * tolerance_pct / 100.0 + 1e-9


def numeric_match(answer: AgentAnswer, expected: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Every expected value must appear in the answer within tolerance (unit-aware)."""
    found = parse_numbers(answer.answer_markdown)
    missing: list[str] = []
    for exp in expected:
        targets = [float(exp["value_base"])] + [float(a) for a in exp.get("alternatives", [])]
        tol = float(exp.get("tolerance_pct", 0.5))
        ok = False
        for value, had_unit, is_pct in found:
            if exp.get("kind") == "ratio":
                candidates = [value] if is_pct else [value, value / 100.0]
            elif is_pct:
                continue
            else:
                # bare numbers are usually in the document's unit (millions) or thousands
                candidates = [value] if had_unit else [value, value * 1e6, value * 1e3, value * 1e9]
            if any(_close(c, t, tol) for c in candidates for t in targets):
                ok = True
                break
        if not ok:
            label = f"{exp.get('legal_entity_contains', '')} {exp.get('metric', '')} FY{exp.get('fiscal_year', '')}={exp['value_base']}"
            missing.append(label.strip())
    return (not missing), missing


def behaviour_match(answer: AgentAnswer, expected: str) -> bool:
    """answer → answered; refuse → not answered; caveat → caveats present or not answered."""
    if expected == "answer":
        return answer.answered
    if expected == "refuse":
        return not answer.answered
    if expected == "caveat":
        return bool(answer.caveats) or not answer.answered
    return True


def retrieval_hit(
    answer: AgentAnswer, trace: Trace, must_cite: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    """Each must_cite document (and page, if listed) appears in citations, retrieved chunks or facts used."""
    missing: list[str] = []
    for req in must_cite:
        name = req.get("file_name_contains", "").lower()
        pages = set(req.get("pages_any_of") or [])
        seen = False
        for c in answer.citations:
            if name in c.file_name.lower() and (not pages or c.page in pages):
                seen = True
        for r in trace.retrieved:
            doc = str(r.get("file_name") or r.get("document_id") or "").lower()
            if name in doc and (not pages or r.get("page") in pages):
                seen = True
        for f in trace.facts_used:
            if name in str(f.get("file_name", "")).lower() and (
                not pages or f.get("page") in pages
            ):
                seen = True
        if not seen:
            missing.append(name)
    return (not missing), missing


def citation_present(answer: AgentAnswer) -> bool:
    """At least one citation when the question was answered."""
    return bool(answer.citations) or not answer.answered


def noise_leak(
    answer: AgentAnswer, doc_types_by_file: dict[str, str], forbidden: list[str]
) -> bool:
    """True when a citation points at a forbidden doc_type (e.g. news)."""
    return any(doc_types_by_file.get(c.file_name) in forbidden for c in answer.citations)


def mentions(
    answer: AgentAnswer, must_any: list[str], must_all: list[str], must_not: list[str]
) -> tuple[bool, list[str]]:
    """Case-insensitive substring checks over the answer text and caveats."""
    text = (answer.answer_markdown + "\n" + "\n".join(answer.caveats)).lower()
    problems: list[str] = []
    if must_any and not any(s.lower() in text for s in must_any):
        problems.append(f"none of {must_any}")
    for s in must_all:
        if s.lower() not in text:
            problems.append(f"missing {s!r}")
    for s in must_not:
        if s.lower() in text:
            problems.append(f"forbidden {s!r}")
    return (not problems), problems


def score_case(
    answer: AgentAnswer, trace: Trace, expect: dict[str, Any], doc_types_by_file: dict[str, str]
) -> dict[str, Any]:
    """All scorers for one case; ``passed`` is the conjunction of the applicable checks."""
    behaviour_ok = behaviour_match(answer, expect.get("behaviour", "answer"))
    numeric_ok, missing_values = numeric_match(answer, expect.get("values", []))
    retrieval_ok, missing_docs = retrieval_hit(answer, trace, expect.get("must_cite", []))
    cite_ok = citation_present(answer)
    leak = noise_leak(answer, doc_types_by_file, expect.get("must_not_cite_doc_types", []))
    mention_ok, mention_problems = mentions(
        answer,
        expect.get("must_mention_any", []),
        expect.get("must_mention_all", []),
        expect.get("must_not_mention", []),
    )
    passed = behaviour_ok and numeric_ok and retrieval_ok and cite_ok and not leak and mention_ok
    return {
        "passed": passed,
        "behaviour": behaviour_ok,
        "numeric": numeric_ok,
        "numeric_missing": missing_values,
        "numeric_applicable": bool(expect.get("values")),
        "retrieval": retrieval_ok,
        "retrieval_missing": missing_docs,
        "citation": cite_ok,
        "noise_leak": leak,
        "mentions": mention_ok,
        "mention_problems": mention_problems,
    }
