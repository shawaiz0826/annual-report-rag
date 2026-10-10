"""Terminal interface: `python -m apps.cli --help`."""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from core.models import AgentAnswer, Trace
from core.service import ChatService

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="Annual-report analyst assistant"
)
console = Console()


@app.command()
def catalog() -> None:
    """Show the discovered document catalog."""
    service = ChatService()
    docs = service.catalog.documents()
    if not docs:
        console.print("[yellow]Catalog is empty — run `make ingest` first.[/yellow]")
        raise typer.Exit(1)
    table = Table(title="Document catalog (discovered at ingest)")
    for col in (
        "issuer",
        "legal entity",
        "type",
        "entity",
        "primary",
        "FY",
        "ccy",
        "unit",
        "pages",
        "chunks",
        "facts",
        "file",
    ):
        table.add_column(col)
    for d in docs:
        s = service.catalog.stats(d.document_id)
        table.add_row(
            d.issuer or "-",
            d.legal_entity or "-",
            d.doc_type,
            d.entity_type or "-",
            "yes" if d.is_primary_issuer_report else "no",
            str(d.fiscal_year or "-"),
            d.reporting_currency or "-",
            d.reporting_unit or "-",
            str(d.page_count),
            str(s.get("chunks", "-")),
            str(s.get("facts", "-")),
            d.file_name,
        )
    console.print(table)
    cov = service.catalog.coverage()
    if cov:
        console.print("[bold]Coverage[/bold] (legal entity → fiscal years of documents held):")
        for entity, years in cov.items():
            console.print(f"  {entity}: {', '.join(map(str, years))}")


@app.command()
def facts(
    issuer: str | None = typer.Argument(None, help="issuer, e.g. Tesla"),
    metric: str | None = typer.Argument(None, help="metric name or alias, e.g. revenue"),
    years: str | None = typer.Argument(None, help="comma-separated fiscal years"),
    all_rows: bool = typer.Option(False, "--all", help="include non-canonical / unusable rows"),
) -> None:
    """Show facts from the store: `facts Tesla revenue 2021,2022,2023`."""
    service = ChatService()
    resolved = service.registry.resolve(metric) if metric else None
    if metric and not resolved:
        console.print(
            f"[red]Unknown metric '{metric}'. Known: {', '.join(service.registry.names())}[/red]"
        )
        raise typer.Exit(1)
    year_list = [int(y) for y in years.split(",")] if years else None
    rows = service.fact_store.lookup(
        issuer=service.catalog.resolve_issuer(issuer) if issuer else None,
        metric=resolved,
        years=year_list,
        canonical_only=not all_rows,
        usable_only=not all_rows,
    )
    if not rows:
        console.print(
            "[yellow]No facts match.[/yellow] Coverage: " + str(service.fact_store.coverage(issuer))
        )
        raise typer.Exit(1)
    table = Table(title=f"Facts ({len(rows)})")
    for col in (
        "legal entity",
        "metric",
        "FY",
        "value",
        "unit",
        "ccy",
        "page",
        "source FY",
        "conf",
        "note",
        "file",
    ):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r.legal_entity,
            r.metric,
            str(r.fiscal_year),
            f"{r.value_reported:,.0f}",
            r.unit_reported,
            r.currency,
            str(r.page),
            str(r.source_fiscal_year) + (" (comp.)" if r.is_comparative else ""),
            f"{r.confidence:.2f}",
            (r.reconciliation_note or "")[:40],
            r.file_name,
        )
    console.print(table)


@app.command("extract-facts")
def extract_facts(
    force: bool = typer.Option(False, help="re-extract even if raw facts exist"),
) -> None:
    """(Re)run fact extraction + reconciliation for catalogued documents (no re-profiling)."""
    from core.llm.client import QuotaExhaustedError

    service = ChatService()
    try:
        counts = service.extract_facts(force=force)
    except QuotaExhaustedError as exc:
        console.print(f"[red]Stopped: {exc}[/red]")
        console.print(
            "Partial results were reconciled; re-run `make extract-facts` later to finish."
        )
        raise typer.Exit(2) from None
    for name, n in counts.items():
        console.print(f"  {name}: {n} raw facts")
    console.print(service.index_stats())


@app.command()
def reprocess() -> None:
    """Re-clean, re-chunk and re-index all documents from source files, keeping profiles (no LLM)."""
    service = ChatService()
    for name, n in service.reprocess().items():
        console.print(f"  {name}: {n} chunks")
    console.print(service.index_stats())


@app.command()
def reindex() -> None:
    """Rebuild vector + BM25 indexes from stored chunks (no LLM calls)."""
    service = ChatService()
    counts = service.reindex()
    for name, n in counts.items():
        console.print(f"  {name}: {n} chunks")
    console.print(service.index_stats())


@app.command()
def ingest(
    path: Path | None = typer.Option(None, help="file or directory (default: data/raw)"),
    force: bool = typer.Option(False, help="re-ingest documents already catalogued"),
) -> None:
    """Ingest documents (idempotent)."""
    import sys

    from scripts.ingest import main as ingest_main  # reuse the script's rendering

    sys.argv = ["ingest"] + (["--path", str(path)] if path else []) + (["--force"] if force else [])
    raise typer.Exit(ingest_main())


if __name__ == "__main__":
    app()


HELP = """Commands:
  /why              explain the last answer (tool calls, chunks with scores, facts, cost)
  /catalog          documents discovered at ingest
  /facts <issuer> [metric] [years]   e.g. /facts Tesla revenue 2021,2022,2023
  /model <id>       switch model for the next turns (no argument: show current)
  /reset            start a new session (clears follow-up context)
  /replay on|off    serve recorded answers when available
  /knowledge on|off toggle the labelled model-knowledge note
  /help, /quit"""


def render_answer(console: Console, answer: AgentAnswer, trace: Trace) -> None:
    """Pretty-print an answer: Markdown panel, citations, caveats, footer."""
    title = "Answer" if answer.answered else "Not answerable from the documents"
    console.print(
        Panel(
            Markdown(answer.answer_markdown),
            title=title,
            border_style="green" if answer.answered else "red",
        )
    )
    if answer.model_knowledge_note:
        console.print(
            Panel(
                answer.model_knowledge_note,
                title="Model knowledge (unverified)",
                border_style="magenta",
            )
        )
    if answer.citations:
        console.print("[bold]Sources[/bold]")
        for c in answer.citations:
            console.print(f"  • {c.label()} — {c.file_name} ({c.kind} {c.ref})")
    if answer.caveats:
        console.print(
            Panel(
                "\n".join(f"• {c}" for c in answer.caveats), title="Caveats", border_style="yellow"
            )
        )
    u = trace.usage
    cost = f"${u.cost_usd:.4f}" if u.cost_usd is not None else "n/a"
    replay = " · [replay]" if trace.replayed else ""
    console.print(
        f"[dim]{trace.model} · {u.prompt_tokens}/{u.completion_tokens} tokens · {cost} · "
        f"{trace.latency_ms / 1000:.1f}s · confidence {answer.confidence} · trace {trace.trace_id}{replay}[/dim]"
    )


class _HttpClient:
    """Minimal client so `--api-url` behaves like the in-process service."""

    def __init__(self, base_url: str) -> None:
        import httpx

        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=300)

    def chat(self, session_id, message, *, model=None, show_model_knowledge=None):
        r = self._http.post(
            "/chat",
            json={
                "session_id": session_id,
                "message": message,
                "model": model,
                "show_model_knowledge": show_model_knowledge,
            },
        )
        r.raise_for_status()
        data = r.json()
        return AgentAnswer.model_validate(data["answer"]), Trace.model_validate(data["trace"])

    def explain(self, trace_id=None):
        r = self._http.get(f"/traces/{trace_id}" if trace_id else "/traces/latest")
        r.raise_for_status()
        return r.json().get("rendered", "")

    def new_session(self):
        r = self._http.post("/sessions")
        r.raise_for_status()

        class _S:
            session_id = r.json()["session_id"]

        return _S()


@app.command()
def chat(
    model: str | None = typer.Option(None, help="model id override"),
    api_url: str | None = typer.Option(None, help="use the HTTP API instead of in-process"),
    show_model_knowledge: bool = typer.Option(False, help="add a labelled model-knowledge note"),
    replay: bool = typer.Option(False, help="serve recorded answers from the replay cache"),
    record: bool = typer.Option(False, help="record answers into the replay cache"),
) -> None:
    """Interactive terminal chat (the demo)."""
    service = _HttpClient(api_url) if api_url else ChatService()
    if not api_url:
        service.replay_enabled = replay
        service.record_enabled = record
    session_id = service.new_session().session_id
    current_model = model or (service.settings.llm_model if not api_url else None)
    knowledge = show_model_knowledge
    console.print(
        Panel(
            f"Annual-report analyst · model [bold]{current_model or 'server default'}[/bold] · session {session_id}\nType a question, or /help.",
            border_style="blue",
        )
    )
    last_trace_id: str | None = None
    while True:
        try:
            text = console.input("[bold cyan]you ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit", "/q"):
            break
        if text == "/help":
            console.print(HELP)
            continue
        if text == "/why":
            console.print(Panel(service.explain(last_trace_id), title="Why", border_style="blue"))
            continue
        if text == "/catalog":
            catalog()
            continue
        if text.startswith("/facts"):
            parts = text.split()[1:]
            with contextlib.suppress(typer.Exit):
                facts(*(parts + [None] * (3 - len(parts)))[:3])
            continue
        if text.startswith("/model"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                current_model = parts[1].strip()
                console.print(f"model → [bold]{current_model}[/bold]")
            else:
                console.print(f"model: {current_model}")
            continue
        if text == "/reset":
            session_id = service.new_session().session_id
            console.print(f"new session {session_id}")
            continue
        if text.startswith("/replay"):
            flag = text.split()[-1].lower() == "on"
            if not api_url:
                service.replay_enabled = flag
            console.print(f"replay {'on' if flag else 'off'}")
            continue
        if text.startswith("/knowledge"):
            knowledge = text.split()[-1].lower() == "on"
            console.print(f"model knowledge {'on' if knowledge else 'off'}")
            continue
        with console.status("thinking…", spinner="dots"):
            try:
                answer, trace = service.chat(
                    session_id, text, model=current_model, show_model_knowledge=knowledge
                )
            except Exception as exc:  # keep the demo alive
                console.print(f"[red]error: {exc}[/red]")
                continue
        last_trace_id = trace.trace_id
        render_answer(console, answer, trace)
