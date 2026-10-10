"""Score the facts store against hand-verified ground truth: `python -m eval.verify_facts`.

Writes ``eval/verification.md``. Accuracy = verified facts whose canonical stored value
matches (within tolerance, alternatives allowed) / verified facts. Also lists stored
canonical facts that have no ground truth, for manual spot checks.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import UNIT_MULTIPLIER  # noqa: E402
from core.service import ChatService  # noqa: E402

ROOT = Path(__file__).resolve().parent
TRUTH = ROOT / "verified_facts.json"
OUT = ROOT / "verification.md"


def main(tolerance_pct: float = 0.5) -> int:
    service = ChatService()
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))["facts"]
    stored = service.fact_store.lookup(canonical_only=True, usable_only=False)
    by_key = {(f.legal_entity.lower(), f.metric, f.fiscal_year): f for f in stored}

    rows = []
    correct = 0
    for t in truth:
        key = (t["legal_entity"].lower(), t["metric"], t["fiscal_year"])
        expected_base = t["value"] * UNIT_MULTIPLIER[t["unit"]]
        alternatives = [a * UNIT_MULTIPLIER[t["unit"]] for a in t.get("alternatives", [])]
        found = by_key.get(key)
        if found is None:
            rows.append((t, None, "missing"))
            continue
        ok = any(
            abs(found.value_base - target) <= abs(target) * tolerance_pct / 100
            for target in [expected_base, *alternatives]
        )
        correct += int(ok)
        rows.append((t, found, "ok" if ok else "WRONG"))
    accuracy = correct / len(truth) if truth else 0.0
    truth_keys = {(t["legal_entity"].lower(), t["metric"], t["fiscal_year"]) for t in truth}
    unverified = [
        f for f in stored if (f.legal_entity.lower(), f.metric, f.fiscal_year) not in truth_keys
    ]

    lines = [
        f"# Fact extraction verification — {datetime.now(UTC).strftime('%Y-%m-%d')}",
        "",
        f"Ground truth: {len(truth)} figures read by hand from the extracted page text (`eval/verified_facts.json`).",
        f"Stored canonical facts: {len(stored)}. **Accuracy on verified figures: {correct}/{len(truth)} = {accuracy * 100:.1f}%** "
        f"(tolerance {tolerance_pct}%; attributable/consolidated alternatives accepted).",
        "",
        "| status | legal entity | metric | FY | expected | stored | unit | page | conf | note |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t, f, status in rows:
        exp = f"{t['value']:,} {t['unit']}"
        if f is None:
            lines.append(
                f"| ❌ missing | {t['legal_entity']} | {t['metric']} | {t['fiscal_year']} | {exp} | – | – | – | – | {t['source']} |"
            )
        else:
            icon = "✅" if status == "ok" else "❌ WRONG"
            lines.append(
                f"| {icon} | {t['legal_entity']} | {t['metric']} | {t['fiscal_year']} | {exp} | {f.value_reported:,.0f} | {f.unit_reported} | {f.page} | {f.confidence:.2f} | {f.reconciliation_note or ''} |"
            )
    lines += [
        "",
        f"## Stored canonical facts without ground truth ({len(unverified)}) — spot-check candidates",
        "",
        "| legal entity | metric | FY | value | unit | page | file | conf |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in unverified:
        lines.append(
            f"| {f.legal_entity} | {f.metric} | {f.fiscal_year} | {f.value_reported:,.0f} | {f.unit_reported} | {f.page} | {f.file_name} | {f.confidence:.2f} |"
        )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"accuracy {correct}/{len(truth)} = {accuracy * 100:.1f}% → {OUT}")
    for t, f, status in rows:
        if status != "ok":
            print(
                f"  {status:>8} {t['legal_entity']} {t['metric']} {t['fiscal_year']}: expected {t['value']:,} {t['unit']}"
                + (f", stored {f.value_reported:,.0f} {f.unit_reported} (p.{f.page})" if f else "")
            )
    return 0 if accuracy >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
