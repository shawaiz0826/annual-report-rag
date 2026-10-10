"""Derived metrics computed in Python (never by the LLM) with ``simpleeval``."""

from __future__ import annotations

import math
from typing import Any

from simpleeval import EvalWithCompoundTypes

from core.facts.registry import DerivedDef, Registry
from core.models import ComputeResult, FactRecord


class CurrencyMismatch(ValueError):  # noqa: N818 - name fixed by the execution plan
    """Raised when a formula would combine amounts in different currencies."""

    def __init__(self, currencies: dict[str, str]) -> None:
        self.currencies = currencies
        listing = ", ".join(f"{k}={v}" for k, v in currencies.items())
        super().__init__(
            "inputs are in different currencies and cannot be combined without conversion "
            f"({listing}). Compare ratios (margins, growth rates) instead, or state both figures "
            "in their own currency."
        )


class ComputeError(ValueError):
    """Bad inputs for a derived metric."""


def format_ratio(value: float) -> str:
    """``0.154`` → ``"15.4%"``."""
    return f"{value * 100:.1f}%"


def format_amount(value: float, currency: str) -> str:
    """Base units → ``"USD 12,587.0 million"`` (billions when ≥ 1e9... kept in millions for comparability)."""
    return f"{currency} {value / 1e6:,.1f} million"


def _value(obj: FactRecord | float | int) -> float:
    return float(obj.value_base) if isinstance(obj, FactRecord) else float(obj)


def compute(
    registry: Registry,
    derived_name: str,
    inputs: dict[str, FactRecord | float | int],
    params: dict[str, float | int] | None = None,
) -> ComputeResult:
    """Evaluate a registry formula over fact inputs.

    * Every formula input must be a :class:`FactRecord` (so the result carries fact-id
      provenance); numeric ``params`` (e.g. ``years``) are plain numbers.
    * Facts in different currencies raise :class:`CurrencyMismatch`.
    * Formulas flagged ``same_entity_year`` reject inputs from different entities or years.
    * Division by zero yields a structured error instead of raising.
    """
    definition = _definition(registry, derived_name)
    params = dict(params or {})
    missing = [name for name in definition.inputs if name not in inputs]
    missing += [name for name in definition.params if name not in params]
    if missing:
        return ComputeResult(derived=derived_name, error="missing inputs", missing=missing)

    facts = {k: v for k, v in inputs.items() if isinstance(v, FactRecord)}
    non_facts = [k for k in definition.inputs if k not in facts]
    if non_facts:
        raise ComputeError(f"inputs must be fact records (provenance required): {non_facts}")

    currencies = {k: f.currency for k, f in facts.items()}
    if len(set(currencies.values())) > 1:
        raise CurrencyMismatch(currencies)

    caveats: list[str] = []
    if definition.same_entity_year:
        entities = {f.legal_entity for f in facts.values()}
        years = {f.fiscal_year for f in facts.values()}
        if len(entities) > 1 or len(years) > 1:
            raise ComputeError(
                f"{definition.label} requires inputs from the same entity and fiscal year; "
                f"got entities={sorted(entities)} years={sorted(years)}"
            )
    for fact in facts.values():
        if fact.reconciliation_note and "restated" in fact.reconciliation_note:
            caveats.append(
                f"{fact.metric} FY{fact.fiscal_year} ({fact.legal_entity}): {fact.reconciliation_note}"
            )
        if fact.confidence < 0.7:
            caveats.append(
                f"{fact.metric} FY{fact.fiscal_year} ({fact.legal_entity}) has confidence {fact.confidence:.2f}"
            )

    names: dict[str, Any] = {k: _value(v) for k, v in facts.items()}
    names.update({k: float(v) for k, v in params.items()})
    evaluator = EvalWithCompoundTypes(names=names, functions={"abs": abs, "min": min, "max": max})
    try:
        value = float(evaluator.eval(definition.formula))
    except ZeroDivisionError:
        return ComputeResult(
            derived=derived_name,
            error="division by zero",
            inputs_used=[f.fact_id for f in facts.values()],
        )
    if math.isnan(value) or math.isinf(value):
        return ComputeResult(
            derived=derived_name,
            error="result is not finite",
            inputs_used=[f.fact_id for f in facts.values()],
        )

    currency = next(iter(currencies.values()), "")
    formatted = (
        format_ratio(value) if definition.output == "ratio" else format_amount(value, currency)
    )
    return ComputeResult(
        derived=derived_name,
        value=value,
        formatted=formatted,
        inputs_used=[f.fact_id for f in facts.values()],
        caveats=caveats,
    )


def _definition(registry: Registry, name: str) -> DerivedDef:
    try:
        return registry.derived[name]
    except KeyError as exc:
        raise ComputeError(
            f"unknown derived metric {name!r}; known: {registry.derived_names()}"
        ) from exc
