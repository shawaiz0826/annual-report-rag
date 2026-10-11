"""Golden-set evaluation runner.

    python -m eval.run_eval --model <id> [--cases q01,q05] [--resume]
    python -m eval.run_eval --both            # both configured models + eval/reports/latest.md

Each case runs in a fresh session; multi-turn cases send their earlier turns first.
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
from core.llm.client import QuotaExhaustedError  # noqa: E402
from core.models import AgentAnswer, Trace  # noqa: E402
from core.service import ChatService  # noqa: E402
from eval.scorers import score_case  # noqa: E402

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "golden.json"
REPORTS = ROOT / "reports"
CACHE = REPORTS / "cache"


class QuotaStopError(RuntimeError):
    """Raised by run_model when the provider's quota is exhausted mid-run."""


def _cache_path(model: str) -> Path:
    return CACHE / f"{slug(model)}.json"


def load_cached_rows(model: str) -> dict[str, dict[str, Any]]:
    """Per-case results from an earlier (partial) run of this model."""
    path = _cache_path(model)
    if not path.exists():
        return {}
    return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))}


def save_cached_rows(model: str, rows: dict[str, dict[str, Any]]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(model).write_text(
        json.dumps(list(rows.values()), indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def load_cases(ids: list[str] | None = None) -> list[dict[str, Any]]:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if not ids or c["id"] in ids]


def run_model(
    service: ChatService,
    model: str,
    cases: list[dict[str, Any]],
    *,
    verbose: bool = True,
    resume: bool = False,
) -> dict[str, Any]:
    """Run every case; with ``resume`` cases already in the cache are not re-run.

    Results are cached per case as they complete, so a run cut short by a quota error
    can be continued the next day with ``--resume``. Raises :class:`QuotaStopError` after
    saving when the quota runs out.
    """
    doc_types = {d.file_name: d.doc_type for d in service.catalog.documents()}
    cached = load_cached_rows(model) if resume else {}
    rows: list[dict[str, Any]] = []
    quota_hit = False
    for case in cases:
        hit = cached.get(case["id"])
        if hit and hit.get("answer_obj") and hit.get("trace_obj") and not hit.get("error"):
            # re-score with the current scorers (they are cheap and may have improved)
            answer_obj = AgentAnswer.model_validate(hit["answer_obj"])
            trace_obj = Trace.model_validate(hit["trace_obj"])
            hit["scores"] = score_case(answer_obj, trace_obj, case["expect"], doc_types)
            rows.append(hit)
            if verbose:
                print(f"  {case['id']} cached ({'PASS' if hit['scores']['passed'] else 'FAIL'})")
            continue
        if quota_hit:
            break
        session = service.new_session()
        started = time.perf_counter()
        answer = trace = None
        error = None
        try:
            for turn in case["turns"]:
                answer, trace = service.chat(session.session_id, turn, model=model)
                if trace.error and ("per-day" in trace.error or "credits" in trace.error.lower()):
                    raise QuotaExhaustedError(trace.error)
        except QuotaExhaustedError as exc:
            quota_hit = True
            error = f"quota: {exc}"[:300]
            if verbose:
                print(f"  {case['id']} stopped — quota exhausted; re-run with --resume later")
            continue
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
        row = {
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
            "answer_obj": answer.model_dump(mode="json"),
            "trace_obj": trace.model_dump(mode="json"),
        }
        rows.append(row)
        cached[case["id"]] = row
        save_cached_rows(model, cached)
        if verbose:
            flag = "PASS" if scores["passed"] else "FAIL"
            detail = "" if scores["passed"] else f"  ← {_why(scores)}"
            print(f"  {case['id']} {flag} ({elapsed:.0f}s, {trace.usage.total_tokens} tok){detail}")
    summary = summarise(model, rows)
    summary["complete"] = len(rows) == len(cases) and not quota_hit
    if quota_hit:
        raise QuotaStopError(summary)
    return summary


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
        f"# Eval report — `{s['model']}` — {s['date']}"
        + ("" if s.get("complete", True) else " (PARTIAL — continue with --resume)"),
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
        "--resume", action="store_true", help="skip cases already completed for the model"
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    service = ChatService(session_store=InMemorySessionStore())
    if hasattr(service.provider, "fallback_models"):
        service.provider.fallback_models = []  # a per-model report must be answered by that model
    models = (
        service.settings.models_for_eval()
        if args.both
        else [args.model or service.settings.llm_model]
    )
    cases = load_cases(args.cases.split(",") if args.cases else None)
    REPORTS.mkdir(exist_ok=True)

    summaries = []
    stopped = False
    for model in models:
        print(f"== {model} ({len(cases)} cases)")
        try:
            summary = run_model(service, model, cases, verbose=not args.quiet, resume=args.resume)
        except QuotaStopError as stop:
            summary = stop.args[0]
            stopped = True
        summaries.append(summary)
        stem = REPORTS / f"report_{slug(model)}_{summary['date']}"
        stem.with_suffix(".md").write_text(render_report(summary), encoding="utf-8")
        slim = {
            **summary,
            "rows": [
                {k: v for k, v in r.items() if k not in ("answer_obj", "trace_obj")}
                for r in summary["rows"]
            ],
        }
        stem.with_suffix(".json").write_text(
            json.dumps(slim, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        state = "" if summary.get("complete", True) else " (partial)"
        print(
            f"   passed {summary['passed']}/{summary['cases']}{state} · numeric {summary['numeric_accuracy']} · leaks {summary['noise_leaks']} → {stem.with_suffix('.md')}"
        )
        if stopped:
            print("   quota exhausted — re-run with --resume to continue")
            break
    if len(summaries) > 1 and all(x.get("complete", True) for x in summaries):
        (REPORTS / "latest.md").write_text(render_comparison(summaries), encoding="utf-8")
        print(f"   comparison → {REPORTS / 'latest.md'}")
    return 2 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
