"""Reconciliation of facts extracted from several documents (or pages) for the same figure."""

from __future__ import annotations

from collections import defaultdict

from core.models import UNIT_MULTIPLIER, FactRecord


def _agree(a: float, b: float, tolerance_pct: float) -> bool:
    if a == b:
        return True
    denominator = max(abs(a), abs(b))
    return denominator > 0 and abs(a - b) / denominator * 100.0 <= tolerance_pct


def reconcile(
    records: list[FactRecord], *, tolerance_pct: float = 1.0, min_confidence: float = 0.5
) -> list[FactRecord]:
    """Assign confidence, canonical flags and notes; returns new records.

    Grouping key: ``(legal_entity, metric, fiscal_year)``.

    * Duplicates from the *same document* that agree are merged (first page kept).
    * One source: confidence 0.8 if it is the original-year report, else 0.6 (comparative).
    * Several sources agreeing within ``tolerance_pct``: the original-year record (or the
      most recent report if none) is canonical with confidence 0.95; note "agrees with …".
    * Disagreement: all kept, original-year record canonical with 0.6; note "restated: …".
    * ``usable`` is ``confidence >= min_confidence``.
    """
    groups: dict[tuple[str, str, int], list[FactRecord]] = defaultdict(list)
    for record in records:
        groups[(record.legal_entity, record.metric, record.fiscal_year)].append(record)
    # how many facts each page yielded: statement pages yield many, narrative pages few
    page_richness: dict[tuple[str, int], int] = defaultdict(int)
    for record in records:
        page_richness[(record.document_id, record.page)] += 1

    out: list[FactRecord] = []
    for group in groups.values():
        out.extend(_reconcile_group(group, tolerance_pct, min_confidence, page_richness))
    return out


def _reconcile_group(
    group: list[FactRecord],
    tolerance_pct: float,
    min_confidence: float,
    page_richness: dict[tuple[str, int], int],
) -> list[FactRecord]:
    # 1. within each document: merge agreeing duplicates; on disagreement keep the record
    #    from the richest page (the statements page) and demote the others
    by_doc: dict[str, list[FactRecord]] = defaultdict(list)
    for r in group:
        by_doc[r.document_id].append(r)
    per_doc: list[FactRecord] = []
    demoted: list[FactRecord] = []
    for doc_records in by_doc.values():
        doc_records.sort(key=lambda r: (-page_richness[(r.document_id, r.page)], r.page))
        kept: list[FactRecord] = []
        for r in doc_records:
            twin = next(
                (k for k in kept if _agree(k.value_base, r.value_base, tolerance_pct)), None
            )
            if twin is None:
                kept.append(r.model_copy())
            elif UNIT_MULTIPLIER[r.unit_reported] < UNIT_MULTIPLIER[twin.unit_reported]:
                # same figure printed more precisely elsewhere (17,937 million beats "$18 billion")
                finer = r.model_copy()
                finer.reconciliation_note = _append(
                    twin.reconciliation_note, f"also on p.{twin.page} (rounded)"
                )
                kept[kept.index(twin)] = finer
            elif r.page != twin.page:
                twin.reconciliation_note = _append(twin.reconciliation_note, f"also on p.{r.page}")
        primary, *others = kept
        per_doc.append(primary)
        for o in others:
            o.canonical = False
            o.confidence = 0.4
            o.usable = o.confidence >= min_confidence
            o.reconciliation_note = _append(
                o.reconciliation_note,
                f"differs from p.{primary.page} of the same report "
                f"({primary.value_reported:,.0f} {primary.unit_reported}); possibly a different line item",
            )
            demoted.append(o)

    # 2. cross-document reconciliation
    def is_original(r: FactRecord) -> bool:
        return r.source_fiscal_year == r.fiscal_year

    per_doc.sort(key=lambda r: (not is_original(r), -r.source_fiscal_year, r.page))
    if len(per_doc) == 1:
        only = per_doc[0]
        only.confidence = 0.8 if is_original(only) else 0.6
        only.canonical = True
        only.usable = only.confidence >= min_confidence
        return [only, *demoted]

    canonical = per_doc[0]
    others = per_doc[1:]
    agreeing = [o for o in others if _agree(canonical.value_base, o.value_base, tolerance_pct)]
    disagreeing = [o for o in others if o not in agreeing]

    if not disagreeing:
        canonical.confidence = 0.95
        notes = [f"agrees with FY{o.source_fiscal_year} report" for o in agreeing]
        canonical.reconciliation_note = _append(
            canonical.reconciliation_note, "; ".join(sorted(set(notes)))
        )
        for o in agreeing:
            o.confidence = 0.9
            o.canonical = False
            o.reconciliation_note = _append(
                o.reconciliation_note, f"agrees with FY{canonical.source_fiscal_year} report"
            )
    else:
        canonical.confidence = 0.6
        notes = [
            f"restated: FY{o.source_fiscal_year} report shows {o.value_reported:,.0f} {o.unit_reported}"
            for o in disagreeing
        ]
        canonical.reconciliation_note = _append(canonical.reconciliation_note, "; ".join(notes))
        for o in disagreeing:
            o.confidence = 0.5
            o.canonical = False
            o.reconciliation_note = _append(
                o.reconciliation_note,
                f"differs from FY{canonical.source_fiscal_year} report ({canonical.value_reported:,.0f} {canonical.unit_reported})",
            )
        for o in agreeing:
            o.confidence = 0.6
            o.canonical = False
    canonical.canonical = True
    for r in per_doc:
        r.usable = r.confidence >= min_confidence
    return [*per_doc, *demoted]


def _append(existing: str | None, note: str) -> str:
    return f"{existing}; {note}" if existing else note
