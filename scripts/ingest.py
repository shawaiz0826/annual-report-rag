"""Ingest documents: `python scripts/ingest.py [--path data/raw] [--force]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from core.service import ChatService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest documents into the catalog and indexes")
    parser.add_argument(
        "--path", type=Path, default=None, help="file or directory (default: config raw_dir)"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-ingest documents already catalogued"
    )
    args = parser.parse_args()

    service = ChatService()
    report = service.ingest(args.path, force=args.force)

    console = Console()
    table = Table(title="Ingestion summary", show_lines=False)
    for col in (
        "file",
        "status",
        "entity",
        "type",
        "FY",
        "pages",
        "chunks",
        "facts",
        "s",
        "warnings",
    ):
        table.add_column(col)
    for r in report.results:
        warn = r.error or "; ".join(r.warnings)
        style = {"ingested": "green", "skipped": "dim", "failed": "red"}[r.status]
        table.add_row(
            r.file_name,
            f"[{style}]{r.status}[/{style}]",
            r.legal_entity or "-",
            r.doc_type or "-",
            str(r.fiscal_year or "-"),
            f"{r.pages_kept}/{r.pages}" if r.pages_kept else str(r.pages),
            str(r.chunks),
            str(r.facts),
            f"{r.seconds:.0f}",
            warn[:80],
        )
    console.print(table)
    u = report.usage
    cost = f"${u.cost_usd:.4f}" if u.cost_usd is not None else "n/a"
    console.print(
        f"{report.counts()} · tokens in/out {u.prompt_tokens}/{u.completion_tokens} · cost {cost}"
    )
    return 1 if report.counts().get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
