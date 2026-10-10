"""Facts layer without an LLM: registry aliases, compute, reconcile, SQLite store."""

from pathlib import Path

import pytest

from core.facts.compute import ComputeError, CurrencyMismatch, compute
from core.facts.reconcile import reconcile
from core.facts.registry import load_registry, normalise_alias
from core.facts.store import FactStore
from core.models import FactRecord


def fact(**kw) -> FactRecord:
    base = dict(
        fact_id="f",
        issuer="Acme",
        legal_entity="Acme plc",
        metric="revenue",
        fiscal_year=2023,
        value_reported=1000.0,
        unit_reported="millions",
        currency="EUR",
        value_base=1e9,
        document_id="doc23",
        page=10,
        source_fiscal_year=2023,
        is_comparative=False,
        confidence=0.8,
    )
    base.update(kw)
    if "value_base" not in kw and "value_reported" in kw:
        mult = {"units": 1, "thousands": 1e3, "millions": 1e6, "billions": 1e9}[
            base["unit_reported"]
        ]
        base["value_base"] = base["value_reported"] * mult
    return FactRecord(**base)


# ------------------------------------------------------------------ registry


def test_registry_loads_and_resolves_aliases():
    reg = load_registry()
    assert "revenue" in reg.names() and "ebitda" in reg.names()
    assert reg.resolve("Total revenues") == "revenue"
    assert reg.resolve("NET INCOME/(LOSS) for the year") == "net_income"
    assert reg.resolve("Income before income taxes") == "profit_before_tax"
    assert (
        reg.resolve("Net income attributable to common stockholders (in millions)") == "net_income"
    )
    assert reg.resolve("Group profit / loss before financial result (EBIT)") == "operating_income"
    assert reg.resolve("something unrelated") is None
    assert normalise_alias("Net income/(loss)") == "net income loss"


def test_registry_has_no_dataset_specific_strings():
    text = Path("core/facts/registry.yaml").read_text().lower()
    assert not any(word in text for word in ("bmw", "ford", "tesla"))


# ------------------------------------------------------------------ compute


def test_net_margin_computes_ratio_with_provenance():
    reg = load_registry()
    rev = fact(fact_id="r", metric="revenue", value_reported=96_773)
    ni = fact(fact_id="n", metric="net_income", value_reported=14_974)
    out = compute(reg, "net_margin", {"net_income": ni, "revenue": rev})
    assert out.error is None and abs(out.value - 14_974 / 96_773) < 1e-9
    assert out.formatted == "15.5%" and set(out.inputs_used) == {"r", "n"}


def test_mixed_units_are_handled_via_base_values():
    reg = load_registry()
    big = fact(fact_id="a", value_reported=2.0, unit_reported="billions")
    small = fact(
        fact_id="b",
        value_reported=500_000,
        unit_reported="thousands",
        fiscal_year=2022,
        document_id="d22",
        source_fiscal_year=2022,
    )
    out = compute(reg, "yoy_growth", {"current": big, "previous": small})
    assert abs(out.value - 3.0) < 1e-9 and out.formatted == "300.0%"


def test_currency_mismatch_raises_for_difference():
    reg = load_registry()
    usd = fact(fact_id="u", currency="USD", legal_entity="Other Inc")
    eur = fact(fact_id="e", currency="EUR")
    with pytest.raises(CurrencyMismatch) as exc:
        compute(reg, "difference", {"a": usd, "b": eur})
    assert "different currencies" in str(exc.value)


def test_same_entity_year_enforced_and_missing_inputs_reported():
    reg = load_registry()
    rev = fact(fact_id="r", metric="revenue")
    ni = fact(
        fact_id="n",
        metric="net_income",
        fiscal_year=2022,
        document_id="d22",
        source_fiscal_year=2022,
    )
    with pytest.raises(ComputeError):
        compute(reg, "net_margin", {"net_income": ni, "revenue": rev})
    out = compute(reg, "net_margin", {"revenue": rev})
    assert out.error == "missing inputs" and out.missing == ["net_income"]
    out = compute(reg, "cagr", {"end": rev, "start": rev})
    assert out.missing == ["years"]


def test_division_by_zero_is_structured():
    reg = load_registry()
    zero = fact(fact_id="z", metric="revenue", value_reported=0.0, value_base=0.0)
    out = compute(
        reg, "net_margin", {"net_income": fact(fact_id="n", metric="net_income"), "revenue": zero}
    )
    assert out.error == "division by zero"


def test_restated_input_adds_caveat():
    reg = load_registry()
    rev = fact(
        fact_id="r",
        metric="revenue",
        reconciliation_note="restated: FY2024 report shows 1,100 millions",
    )
    out = compute(reg, "ratio", {"a": rev, "b": fact(fact_id="b", metric="net_income")})
    assert any("restated" in c for c in out.caveats)


# ------------------------------------------------------------------ reconcile


def test_single_original_and_comparative_confidence():
    orig = fact(fact_id="o")
    comp = fact(
        fact_id="c",
        fiscal_year=2022,
        document_id="doc23",
        source_fiscal_year=2023,
        is_comparative=True,
    )
    out = {r.fact_id: r for r in reconcile([orig, comp])}
    assert out["o"].confidence == 0.8 and out["c"].confidence == 0.6
    assert all(r.canonical and r.usable for r in out.values())


def test_agreeing_sources_marks_original_canonical_with_note():
    orig = fact(
        fact_id="o",
        fiscal_year=2022,
        document_id="d22",
        source_fiscal_year=2022,
        value_reported=81_462,
    )
    comp = fact(
        fact_id="c",
        fiscal_year=2022,
        document_id="d23",
        source_fiscal_year=2023,
        is_comparative=True,
        value_reported=81_462,
    )
    out = {r.fact_id: r for r in reconcile([comp, orig])}
    assert out["o"].canonical and out["o"].confidence == 0.95
    assert out["o"].reconciliation_note == "agrees with FY2023 report"
    assert not out["c"].canonical and "agrees with FY2022" in out["c"].reconciliation_note


def test_disagreeing_sources_flag_restatement():
    orig = fact(
        fact_id="o",
        fiscal_year=2022,
        document_id="d22",
        source_fiscal_year=2022,
        value_reported=100,
    )
    comp = fact(
        fact_id="c",
        fiscal_year=2022,
        document_id="d23",
        source_fiscal_year=2023,
        is_comparative=True,
        value_reported=120,
    )
    out = {r.fact_id: r for r in reconcile([orig, comp])}
    assert out["o"].canonical and out["o"].confidence == 0.6
    assert out["o"].reconciliation_note.startswith("restated: FY2023 report shows 120")
    assert not out["c"].canonical and out["c"].usable  # 0.5 >= 0.5


def test_same_document_duplicates_merge_and_tolerance():
    a = fact(fact_id="a", page=10, value_reported=1000)
    b = fact(fact_id="b", page=45, value_reported=1004)  # within 1%
    out = reconcile([a, b])
    assert len(out) == 1 and out[0].fact_id == "a" and "also on p.45" in out[0].reconciliation_note


# ------------------------------------------------------------------ store


def test_store_roundtrip_lookup_coverage_export(tmp_path: Path):
    store = FactStore(tmp_path / "facts.db")
    recs = reconcile(
        [
            fact(fact_id="r23", metric="revenue"),
            fact(
                fact_id="r22",
                metric="revenue",
                fiscal_year=2022,
                document_id="d23",
                source_fiscal_year=2023,
                is_comparative=True,
            ),
            fact(fact_id="n23", metric="net_income", value_reported=100),
            fact(fact_id="x", issuer="Other", legal_entity="Other Inc", currency="USD"),
        ]
    )
    store.replace_all(recs)
    assert store.count() == 4
    assert [r.fiscal_year for r in store.lookup(issuer="acme", metric="revenue")] == [2022, 2023]
    assert (
        store.lookup(legal_entity="Acme plc", years=[2023], metric="net_income")[0].value_base
        == 1e8
    )
    assert store.coverage(issuer="Acme") == {
        "Acme plc": {"net_income": [2023], "revenue": [2022, 2023]}
    }
    assert store.export_csv(tmp_path / "facts.csv") == 4
    assert (tmp_path / "facts.csv").read_text().count("\n") == 5
    store.delete_document("doc23")
    assert store.count() == 1
    store.close()
