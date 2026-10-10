"""Terminal interface: `python -m apps.cli --help`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

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
    service = ChatService()
    counts = service.extract_facts(force=force)
    for name, n in counts.items():
        console.print(f"  {name}: {n} raw facts")
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
