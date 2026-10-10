"""Hybrid search from the terminal: `python scripts/search.py "query" [--issuer BMW] [-k 8]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from core.service import ChatService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Hybrid (dense + BM25) search over indexed chunks")
    parser.add_argument("query")
    parser.add_argument("--issuer", action="append", help="restrict to issuer(s); repeatable")
    parser.add_argument("--entity", help="restrict to a legal entity")
    parser.add_argument("--doc-type", help="restrict to a doc_type (annual_report, news, ...)")
    parser.add_argument("-k", type=int, default=None, help="number of results")
    parser.add_argument("--show", type=int, default=240, help="characters of text to show per hit")
    args = parser.parse_args()

    service = ChatService()
    filters = {}
    if args.entity:
        filters["legal_entity"] = args.entity
    if args.doc_type:
        filters["doc_type"] = args.doc_type
    results = service.search(args.query, issuers=args.issuer, filters=filters or None, k=args.k)

    console = Console()
    table = Table(title=f"'{args.query}'  issuers={args.issuer or 'any'}", show_lines=True)
    for col in ("#", "rrf", "dense", "bm25", "entity", "FY", "page", "type", "text"):
        table.add_column(col)
    for i, r in enumerate(results, start=1):
        m = r.chunk.metadata
        body = r.chunk.text.split("\n", 1)[-1].replace("\n", " ")[: args.show]
        table.add_row(
            str(i),
            f"{r.rrf_score:.4f}",
            str(r.dense_rank or "-"),
            str(r.lexical_rank or "-"),
            str(m.get("legal_entity") or m.get("doc_type")),
            str(m.get("fiscal_year") or "-"),
            str(r.chunk.page),
            r.chunk.chunk_type + ("+page" if r.expanded_text else ""),
            body,
        )
    console.print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
