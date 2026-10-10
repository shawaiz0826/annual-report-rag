"""Document catalog: what has been ingested, by issuer, entity and fiscal year.

Persisted as JSON in ``data/derived/catalog.json`` (committed — it is a readable
artefact of what the system *discovered*).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from core.models import DocumentProfile


class Catalog:
    """JSON-backed registry of :class:`DocumentProfile` plus per-document stats."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._docs: dict[str, DocumentProfile] = {}
        self._stats: dict[str, dict[str, Any]] = {}
        self.load()

    # ------------------------------------------------------------------ persistence

    def load(self) -> None:
        """Load from disk if the file exists."""
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._docs = {
            d["document_id"]: DocumentProfile.model_validate(d) for d in data.get("documents", [])
        }
        self._stats = data.get("stats", {})

    def save(self) -> None:
        """Write to disk (sorted for stable diffs)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        docs = sorted(
            self._docs.values(), key=lambda d: (d.issuer or "~", d.fiscal_year or 0, d.file_name)
        )
        payload = {
            "documents": [d.model_dump(mode="json") for d in docs],
            "stats": {k: self._stats[k] for k in sorted(self._stats)},
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------------ mutation

    def add(self, profile: DocumentProfile, stats: dict[str, Any] | None = None) -> None:
        """Insert or replace a document."""
        self._docs[profile.document_id] = profile
        if stats is not None:
            self._stats[profile.document_id] = stats

    def set_stats(self, document_id: str, **stats: Any) -> None:
        """Merge stats for a document (chunks, facts, pages ...)."""
        self._stats.setdefault(document_id, {}).update(stats)

    def remove(self, document_id: str) -> None:
        """Forget a document."""
        self._docs.pop(document_id, None)
        self._stats.pop(document_id, None)

    # ------------------------------------------------------------------ queries

    def has(self, document_id: str) -> bool:
        """True if the document is catalogued."""
        return document_id in self._docs

    def get(self, document_id: str) -> DocumentProfile | None:
        """Profile by id."""
        return self._docs.get(document_id)

    def stats(self, document_id: str) -> dict[str, Any]:
        """Stats by id (empty dict if none)."""
        return self._stats.get(document_id, {})

    def documents(self) -> list[DocumentProfile]:
        """All profiles, sorted by issuer, year, name."""
        return sorted(
            self._docs.values(), key=lambda d: (d.issuer or "~", d.fiscal_year or 0, d.file_name)
        )

    def financial_documents(self) -> list[DocumentProfile]:
        """Profiles the facts extractor should process."""
        return [d for d in self.documents() if d.is_financial]

    def issuers(self) -> dict[str, list[str]]:
        """Issuer → sorted list of legal entities seen for it."""
        out: dict[str, set[str]] = defaultdict(set)
        for d in self._docs.values():
            if d.issuer and d.legal_entity:
                out[d.issuer].add(d.legal_entity)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def coverage(self, issuer: str | None = None) -> dict[str, list[int]]:
        """Legal entity → sorted fiscal years of the documents held (optionally for one issuer)."""
        out: dict[str, set[int]] = defaultdict(set)
        for d in self._docs.values():
            if not d.is_financial or not d.legal_entity or d.fiscal_year is None:
                continue
            if issuer and not _same_issuer(d.issuer, issuer):
                continue
            out[d.legal_entity].add(d.fiscal_year)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def resolve_issuer(self, name: str) -> str | None:
        """Map a user-supplied name to a catalogued issuer (case-insensitive, substring-tolerant)."""
        needle = name.strip().lower()
        if not needle:
            return None
        for issuer in self.issuers():
            if issuer.lower() == needle:
                return issuer
        for issuer, entities in self.issuers().items():
            if needle in issuer.lower() or issuer.lower() in needle:
                return issuer
            if any(needle in e.lower() for e in entities):
                return issuer
        return None

    def find(
        self,
        issuer: str | None = None,
        legal_entity: str | None = None,
        fiscal_year: int | None = None,
        doc_type: str | None = None,
    ) -> list[DocumentProfile]:
        """Filter profiles by any combination of fields (issuer match is case-insensitive)."""
        result = []
        for d in self.documents():
            if issuer and not _same_issuer(d.issuer, issuer):
                continue
            if legal_entity and (d.legal_entity or "").lower() != legal_entity.lower():
                continue
            if fiscal_year is not None and d.fiscal_year != fiscal_year:
                continue
            if doc_type and d.doc_type != doc_type:
                continue
            result.append(d)
        return result

    def describe(self) -> dict[str, Any]:
        """The structure returned by the ``list_catalog`` tool."""
        issuers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        other: list[dict[str, Any]] = []
        for d in self.documents():
            entry = {
                "file_name": d.file_name,
                "document_id": d.document_id,
                "doc_type": d.doc_type,
                "legal_entity": d.legal_entity,
                "entity_type": d.entity_type,
                "parent_organisation": d.parent_organisation,
                "is_primary_issuer_report": d.is_primary_issuer_report,
                "fiscal_year": d.fiscal_year,
                "currency": d.reporting_currency,
                "unit": d.reporting_unit,
                "sector": d.sector,
                "summary": d.summary,
            }
            if d.issuer and d.is_financial:
                issuers[d.issuer].append(entry)
            else:
                other.append(entry)
        return {"issuers": dict(issuers), "other_documents": other, "coverage": self.coverage()}


def _same_issuer(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.strip().lower() == b.strip().lower()
