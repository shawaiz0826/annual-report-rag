"""SQLite facts store (stdlib ``sqlite3``) with CSV export."""

from __future__ import annotations

import csv
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path

from core.models import FactRecord

_COLUMNS = [
    "fact_id", "issuer", "legal_entity", "metric", "fiscal_year", "value_reported", "unit_reported",
    "currency", "value_base", "document_id", "file_name", "page", "source_fiscal_year",
    "is_comparative", "metric_alias_seen", "evidence_snippet", "extraction_model",
    "extraction_prompt_version", "confidence", "reconciliation_note", "canonical", "usable", "verified",
]  # fmt: skip

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS facts (
    {", ".join(f"{c} TEXT" if c not in ("fiscal_year", "page", "source_fiscal_year") else f"{c} INTEGER" for c in _COLUMNS)},
    PRIMARY KEY (fact_id)
);
CREATE INDEX IF NOT EXISTS facts_lookup ON facts (issuer, legal_entity, metric, fiscal_year);
CREATE INDEX IF NOT EXISTS facts_doc ON facts (document_id);
"""


class FactStore:
    """One table of :class:`FactRecord` rows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # one connection shared across threads (FastAPI runs sync endpoints in a pool);
        # a lock serialises access, which is plenty at this scale
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ mutation

    def replace_document(self, document_id: str, records: list[FactRecord]) -> None:
        """Replace all facts of a document (raw, pre-reconciliation)."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM facts WHERE document_id = ?", (document_id,))
            self._insert(records)

    def replace_all(self, records: list[FactRecord]) -> None:
        """Replace the whole table (after reconciliation)."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM facts")
            self._insert(records)

    def delete_document(self, document_id: str) -> None:
        """Drop a document's facts."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM facts WHERE document_id = ?", (document_id,))

    def _insert(self, records: list[FactRecord]) -> None:
        rows = [tuple(_to_sql(getattr(r, c)) for c in _COLUMNS) for r in records]
        placeholders = ", ".join("?" for _ in _COLUMNS)
        self._conn.executemany(
            f"INSERT OR REPLACE INTO facts ({', '.join(_COLUMNS)}) VALUES ({placeholders})", rows
        )

    # ------------------------------------------------------------------ queries

    def all(self) -> list[FactRecord]:
        """Every row."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM facts ORDER BY issuer, legal_entity, metric, fiscal_year, page"
            ).fetchall()
        return [_from_row(r) for r in rows]

    def lookup(
        self,
        issuer: str | None = None,
        legal_entity: str | None = None,
        metric: str | None = None,
        years: list[int] | None = None,
        canonical_only: bool = True,
        usable_only: bool = True,
    ) -> list[FactRecord]:
        """Filter facts; string matches are case-insensitive."""
        clauses: list[str] = []
        args: list[object] = []
        if issuer:
            clauses.append("lower(issuer) = lower(?)")
            args.append(issuer)
        if legal_entity:
            clauses.append("lower(legal_entity) = lower(?)")
            args.append(legal_entity)
        if metric:
            clauses.append("metric = ?")
            args.append(metric)
        if years:
            clauses.append(f"fiscal_year IN ({', '.join('?' for _ in years)})")
            args.extend(int(y) for y in years)
        if canonical_only:
            clauses.append("canonical = '1'")
        if usable_only:
            clauses.append("usable = '1'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM facts {where} ORDER BY legal_entity, metric, fiscal_year, canonical DESC, page",
                args,
            ).fetchall()
        return [_from_row(r) for r in rows]

    def coverage(
        self, issuer: str | None = None, metric: str | None = None
    ) -> dict[str, dict[str, list[int]]]:
        """``{legal_entity: {metric: [years]}}`` for canonical usable facts."""
        out: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
        for r in self.lookup(issuer=issuer, metric=metric):
            out[r.legal_entity][r.metric].add(r.fiscal_year)
        return {e: {m: sorted(ys) for m, ys in ms.items()} for e, ms in out.items()}

    def count(self) -> int:
        """Number of rows."""
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])

    def count_by_document(self) -> dict[str, int]:
        """``{document_id: canonical usable facts}`` — what a catalog should show per file."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT document_id, COUNT(*) FROM facts WHERE canonical = '1' AND usable = '1' "
                "GROUP BY document_id"
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def export_csv(self, path: Path) -> int:
        """Write every row to CSV; returns the row count."""
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.all()
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
            writer.writeheader()
            for r in rows:
                writer.writerow({c: getattr(r, c) for c in _COLUMNS})
        return len(rows)

    def import_csv(self, path: Path) -> int:
        """Replace the table with the rows of a CSV written by :meth:`export_csv`."""
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        records = []
        for row in rows:
            data: dict[str, object] = dict(row)
            for key in ("is_comparative", "canonical", "usable", "verified"):
                data[key] = str(data[key]) in ("True", "true", "1")
            for key in ("fiscal_year", "page", "source_fiscal_year"):
                data[key] = int(float(str(data[key])))
            for key in ("value_reported", "value_base", "confidence"):
                data[key] = float(str(data[key]))
            if not data.get("reconciliation_note"):
                data["reconciliation_note"] = None
            records.append(FactRecord.model_validate(data))
        self.replace_all(records)
        return len(records)

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._conn.close()


def _to_sql(value: object) -> object:
    if isinstance(value, bool):
        return "1" if value else "0"
    return value


def _from_row(row: sqlite3.Row) -> FactRecord:
    data = dict(row)
    for key in ("is_comparative", "canonical", "usable", "verified"):
        data[key] = str(data[key]) == "1"
    for key in ("value_reported", "value_base", "confidence"):
        data[key] = float(data[key])
    return FactRecord.model_validate(data)
