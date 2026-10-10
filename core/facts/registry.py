"""Metric registry loader: canonical metrics, alias resolution, derived-metric definitions."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.yaml"
_PUNCT = re.compile(r"[^a-z0-9]+")


def normalise_alias(text: str) -> str:
    """Lower-case, strip punctuation and collapse whitespace: ``"Net income/(loss)"`` → ``"net income loss"``."""
    return _PUNCT.sub(" ", text.lower()).strip()


class MetricDef(BaseModel):
    """One canonical metric."""

    name: str
    label: str
    kind: Literal["flow", "stock"] = "flow"
    aliases: list[str] = Field(default_factory=list)
    search_hints: list[str] = Field(default_factory=list)


class DerivedDef(BaseModel):
    """A formula over named inputs (facts) and optional numeric params."""

    name: str
    label: str
    formula: str
    inputs: list[str]
    params: list[str] = Field(default_factory=list)
    output: Literal["ratio", "amount"] = "ratio"
    same_entity_year: bool = False


class Registry(BaseModel):
    """The parsed registry."""

    version: int
    metrics: dict[str, MetricDef]
    derived: dict[str, DerivedDef]

    @model_validator(mode="after")
    def _check(self) -> Registry:
        seen: dict[str, str] = {}
        for metric in self.metrics.values():
            for alias in [metric.name, metric.label, *metric.aliases]:
                key = normalise_alias(alias)
                if key in seen and seen[key] != metric.name:
                    raise ValueError(f"alias {alias!r} maps to both {seen[key]} and {metric.name}")
                seen[key] = metric.name
        return self

    # ------------------------------------------------------------------ lookups

    def resolve(self, name_or_alias: str) -> str | None:
        """Canonical metric name for ``name_or_alias`` (exact normalised match, then containment)."""
        key = normalise_alias(name_or_alias)
        if not key:
            return None
        if key in self.metrics:
            return key
        table = self._alias_table()
        if key in table:
            return table[key]
        # containment: "net income attributable to common stockholders (in millions)" → net_income
        best: tuple[int, str] | None = None
        for alias, metric in table.items():
            if alias in key and (best is None or len(alias) > best[0]):
                best = (len(alias), metric)
        return best[1] if best else None

    def names(self) -> list[str]:
        """Canonical metric names."""
        return list(self.metrics)

    def derived_names(self) -> list[str]:
        """Derived metric names."""
        return list(self.derived)

    def describe(self) -> dict[str, dict[str, str]]:
        """Short description for tool schemas and the UI."""
        return {
            "metrics": {k: v.label for k, v in self.metrics.items()},
            "derived": {k: f"{v.label}: {v.formula}" for k, v in self.derived.items()},
        }

    @cache  # noqa: B019 - registry is immutable after load
    def _alias_table(self) -> dict[str, str]:
        table: dict[str, str] = {}
        for metric in self.metrics.values():
            for alias in [metric.name, metric.label, *metric.aliases]:
                table[normalise_alias(alias)] = metric.name
        return table

    def __hash__(self) -> int:  # needed for functools.cache on methods
        return id(self)


@cache
def load_registry(path: Path | None = None) -> Registry:
    """Load and validate the YAML registry (cached)."""
    data = yaml.safe_load((path or REGISTRY_PATH).read_text(encoding="utf-8"))
    metrics = {k: MetricDef(name=k, **v) for k, v in (data.get("metrics") or {}).items()}
    derived = {k: DerivedDef(name=k, **v) for k, v in (data.get("derived") or {}).items()}
    return Registry(version=int(data.get("version", 1)), metrics=metrics, derived=derived)
