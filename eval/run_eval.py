"""Golden-set evaluation runner.

    python -m eval.run_eval --model <id> [--cases q01,q05] [--replay] [--record]
    python -m eval.run_eval --both            # both configured models + eval/reports/latest.md

Each case runs in a fresh session; multi-turn cases replay their earlier turns first.
Results are written to ``eval/reports/report_<model-slug>_<date>.md`` and ``.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent.memory import InMemorySessionStore  # noqa: E402
from core.service import ChatService  # noqa: E402
from eval.scorers import score_case  # noqa: E402

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "golden.json"
REPORTS = ROOT / "reports"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def load_cases(ids: list[str] | None = None) -> list[dict[str, Any]]:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if not ids or c["id"] in ids]


def run_model(
    service: ChatService, model: str, cases: list[dict[str, Any]], *, verbose: bool = True
) -> dict[str, Any]:
    doc_types = {d.file_name: d.doc_type for d in service.catalog.documents()}
    rows: list[dict[str, Any]] = []
    for case in cases:
        session = service.new_session()
        started = time.perf_counter()
        answer = trace = None
        error = None
        try:
            for turn in case["turns"]:
                answer, trace = service.chat(session.session_id, turn, model=model)
        except Exception as exc:  # keep going; the row records the failure
            error = f"{type(exc).__name__}: {exc}"[:300]
        elapsed = time.perf_counter() - started
        if answer is None or trace is None:
            scores = {"passed": False, "error": error or "no answer"}
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": None}
            rows.append(
                {
                    "id": case["id"],
                    "category": case["category"],
                    "scores": scores,
                    "usage": usage,
                    "latency_s": elapsed,
                    "trace_id": None,
                    "answer": "",
                    "caveats": [],
                    "error": error,
                }
            )
            if verbose:
                print(f"  {case['id']} FAIL (error: {error})")
            continue
        scores = score_case(answer, trace, case["expect"], doc_types)
        rows.append(
            {
                "id": case["id"],
                "category": case["category"],
                "question": case["turns"][-1],
                "scores": scores,
                "usage": trace.usage.model_dump(),
                "latency_s": round(elapsed, 1),
                "trace_id": trace.trace_id,
                "answer": answer.answer_markdown,
                "caveats": answer.caveats,
                "answered": answer.answered,
                "confidence": answer.confidence,
                "citations": [c.label() for c in answer.citations],
                "error": trace.error,
            }
        )
        if verbose:
            flag = "PASS" if scores["passed"] else "FAIL"
            detail = "" if scores["passed"] else f"  ← {_why(scores)}"
            print(f"  {case['id']} {flag} ({elapsed:.0f}s, {trace.usage.total_tokens} tok){detail}")
    return summarise(model, rows)


def _why(scores: dict[str, Any]) -> str:
    bits = []
    if not scores.get("behaviour", True):
        bits.append("behaviour")
    if scores.get("numeric_missing"):
        bits.append("values " + "; ".join(scores["numeric_missing"]))
    if scores.get("retrieval_missing"):
        bits.append("retrieval " + ", ".join(scores["retrieval_missing"]))
    if not scores.get("citation", True):
        bits.append("no citation")
    if scores.get("noise_leak"):
        bits.append("NOISE LEAK")
    if scores.get("mention_problems"):
        bits.append("mentions " + "; ".join(scores["mention_problems"]))
    if scores.get("error"):
        bits.append(scores["error"])
    return "; ".join(bits)


def summarise(model: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "numeric_applicable" in r["scores"]]
    numeric_rows = [r for r in scored if r["scores"]["numeric_applicable"]]
    by_cat: dict[str, dict[str, int]] = {}
    for r in rows:
        cat = by_cat.setdefault(r["category"], {"passed": 0, "total": 0})
        cat["total"] += 1
        cat["passed"] += int(r["scores"].get("passed", False))
    costs = [r["usage"].get("cost_usd") for r in rows if r["usage"].get("cost_usd") is not None]
    return {
        "model": model,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "cases": len(rows),
        "passed": sum(int(r["scores"].get("passed", False)) for r in rows),
        "numeric_accuracy": (
            sum(int(r["scores"]["numeric"]) for r in numeric_rows) / len(numeric_rows)
        )
        if numeric_rows
        else None,
        "numeric_cases": len(numeric_rows),
        "noise_leaks": sum(int(r["scores"].get("noise_leak", False)) for r in rows),
        "behaviour_accuracy": (sum(int(r["scores"]["behaviour"]) for r in scored) / len(scored))
        if scored
        else None,
        "mean_latency_s": round(statistics.mean(r["latency_s"] for r in rows), 1) if rows else None,
        "mean_tokens": round(
            statistics.mean(
                r["usage"].get("prompt_tokens", 0) + r["usage"].get("completion_tokens", 0)
                for r in rows
            )
        )
        if rows
        else None,
        "total_cost_usd": round(sum(costs), 4) if costs else None,
        "by_category": by_cat,
        "rows": rows,
    }


def render_report(summary: dict[str, Any]) -> str:
    s = summary
    pct = lambda x: "n/a" if x is None else f"{x * 100:.0f}%"  # noqa: E731
    lines = [
        f"# Eval report — `{s['model']}` — {s['date']}",
        "",
        "| cases | passed | numeric accuracy | behaviour accuracy | noise leaks | mean latency | mean tokens | total cost |",
        "|---|---|---|---|---|---|---|---|",
        f"| {s['cases']} | {s['passed']} ({pct(s['passed'] / s['cases'] if s['cases'] else None)}) | {pct(s['numeric_accuracy'])} ({s['numeric_cases']} cases) | {pct(s['behaviour_accuracy'])} | {s['noise_leaks']} | {s['mean_latency_s']}s | {s['mean_tokens']} | {_money(s['total_cost_usd'])} |",
        "",
        "## By category",
        "",
        "| category | passed / total |",
        "|---|---|",
    ]
    for cat, c in sorted(s["by_category"].items()):
        lines.append(f"| {cat} | {c['passed']} / {c['total']} |")
    lines += [
        "",
        "## Cases",
        "",
        "| id | category | pass | behaviour | numeric | retrieval | citation | leak | mentions | latency | tokens | trace |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in s["rows"]:
        sc = r["scores"]
        tick = lambda v: "✅" if v else "❌"  # noqa: E731
        if "numeric_applicable" not in sc:
            lines.append(
                f"| {r['id']} | {r['category']} | ❌ | – | – | – | – | – | – | {r['latency_s']:.0f}s | – | error: {r.get('error')} |"
            )
            continue
        lines.append(
            f"| {r['id']} | {r['category']} | {tick(sc['passed'])} | {tick(sc['behaviour'])} | "
            f"{tick(sc['numeric']) if sc['numeric_applicable'] else '–'} | {tick(sc['retrieval'])} | {tick(sc['citation'])} | "
            f"{'⚠️' if sc['noise_leak'] else '–'} | {tick(sc['mentions'])} | {r['latency_s']:.0f}s | "
            f"{r['usage'].get('prompt_tokens', 0) + r['usage'].get('completion_tokens', 0)} | `{r['trace_id']}` |"
        )
    failures = [r for r in s["rows"] if not r["scores"].get("passed")]
    lines += ["", f"## Failures ({len(failures)})", ""]
    for r in failures:
        lines.append(f"### {r['id']} — {r.get('question', '')}")
        lines.append("")
        lines.append(f"- why: {_why(r['scores'])}")
        lines.append(f"- trace: `{r['trace_id']}`")
        if r.get("answer"):
            lines.append("- answer:")
            lines.append("")
            lines.append("  > " + r["answer"].replace("\n", "\n  > ")[:1500])
        if r.get("caveats"):
            lines.append(f"- caveats: {r['caveats']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_comparison(summaries: list[dict[str, Any]]) -> str:
    pct = lambda x: "n/a" if x is None else f"{x * 100:.0f}%"  # noqa: E731
    lines = [
        f"# Eval comparison — {summaries[0]['date']}",
        "",
        "| model | passed | numeric accuracy | behaviour | noise leaks | mean latency | mean tokens | cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| `{s['model']}` | {s['passed']}/{s['cases']} | {pct(s['numeric_accuracy'])} | {pct(s['behaviour_accuracy'])} | {s['noise_leaks']} | {s['mean_latency_s']}s | {s['mean_tokens']} | {_money(s['total_cost_usd'])} |"
        )
    lines += [
        "",
        "## Per case",
        "",
        "| id | category | " + " | ".join(f"`{s['model']}`" for s in summaries) + " |",
        "|---|---|" + "---|" * len(summaries),
    ]
    ids = [r["id"] for r in summaries[0]["rows"]]
    for cid in ids:
        cells = []
        for s in summaries:
            row = next((r for r in s["rows"] if r["id"] == cid), None)
            cells.append("✅" if row and row["scores"].get("passed") else "❌")
        cat = next(r["category"] for r in summaries[0]["rows"] if r["id"] == cid)
        lines.append(f"| {cid} | {cat} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Full reports: "
        + ", ".join(f"`report_{slug(s['model'])}_{s['date']}.md`" for s in summaries),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the golden-set evaluation")
    parser.add_argument("--model", help="model id (default: configured primary)")
    parser.add_argument(
        "--both", action="store_true", help="run both configured models and write latest.md"
    )
    parser.add_argument("--cases", help="comma-separated case ids")
    parser.add_argument(
        "--replay", action="store_true", help="serve from the replay cache when possible"
    )
    parser.add_argument(
        "--record", action="store_true", help="record answers into the replay cache"
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    service = ChatService(session_store=InMemorySessionStore())
    service.replay_enabled = args.replay
    service.record_enabled = args.record
    models = (
        service.settings.models_for_eval()
        if args.both
        else [args.model or service.settings.llm_model]
    )
    cases = load_cases(args.cases.split(",") if args.cases else None)
    REPORTS.mkdir(exist_ok=True)

    summaries = []
    for model in models:
        print(f"== {model} ({len(cases)} cases)")
        summary = run_model(service, model, cases, verbose=not args.quiet)
        summaries.append(summary)
        stem = REPORTS / f"report_{slug(model)}_{summary['date']}"
        stem.with_suffix(".md").write_text(render_report(summary), encoding="utf-8")
        stem.with_suffix(".json").write_text(
            json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        print(
            f"   passed {summary['passed']}/{summary['cases']} · numeric {summary['numeric_accuracy']} · leaks {summary['noise_leaks']} → {stem.with_suffix('.md')}"
        )
    if args.both or len(summaries) > 1:
        (REPORTS / "latest.md").write_text(render_comparison(summaries), encoding="utf-8")
        print(f"   comparison → {REPORTS / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
