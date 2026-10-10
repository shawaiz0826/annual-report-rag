"""Chunker: window boundaries, overlap, provenance header, table chunks."""

from core.ingestion.chunker import (
    ChunkingOptions,
    FixedWindowChunker,
    split_windows,
    table_to_markdown,
)
from core.models import DocumentProfile, PageText


def _profile() -> DocumentProfile:
    return DocumentProfile(
        document_id="doc1",
        source_path="x.pdf",
        file_name="x.pdf",
        doc_type="annual_report",
        issuer="Acme",
        legal_entity="Acme Holdings plc",
        entity_type="group",
        is_primary_issuer_report=True,
        fiscal_year=2023,
        reporting_currency="GBP",
        reporting_unit="millions",
        sector="Widgets",
    )


def test_split_windows_respects_size_and_overlaps():
    text = " ".join(f"word{i}" for i in range(400))
    windows = split_windows(text, size=300, overlap=60)
    assert all(len(w) <= 300 for w in windows)
    assert len(windows) > 5
    # consecutive windows share content (overlap)
    assert any(windows[i].split()[-1] in windows[i + 1] for i in range(len(windows) - 1))
    # nothing lost
    assert set(text.split()) == {w for win in windows for w in win.split()}


def test_split_prefers_paragraph_breaks():
    text = ("Para one sentence. " * 10).strip() + "\n\n" + ("Para two sentence. " * 10).strip()
    windows = split_windows(text, size=len(text) - 20, overlap=0)
    assert windows[0].endswith("Para one sentence.")


def test_chunks_never_cross_pages_and_carry_header_and_metadata():
    pages = [PageText(page=1, text="A " * 600), PageText(page=2, text="B " * 600)]
    chunks = FixedWindowChunker(ChunkingOptions(chunk_chars=500, overlap_chars=50)).chunk(
        pages, _profile()
    )
    assert {c.page for c in chunks} == {1, 2}
    for c in chunks:
        assert c.text.startswith(
            f"[Acme Holdings plc | annual report FY2023 | GBP millions | p.{c.page}]"
        )
        assert c.metadata["issuer"] == "Acme" and c.metadata["fiscal_year"] == 2023
        assert c.chunk_id.startswith(f"doc1:p{c.page}:c")
        body = c.text.split("\n", 1)[1]
        assert set(body.split()) == ({"A"} if c.page == 1 else {"B"})


def test_table_chunks_rendered_as_markdown_with_heading():
    rows = [
        ["in £ million", "2023", "2022"],
        ["Revenue", "1,000", "900"],
        ["Net income", "100", "90"],
    ]
    page = PageText(
        page=3,
        text="Group results\n" + "Some prose. " * 5 + "\nin £ million 2023 2022\nRevenue 1,000 900",
        tables=[rows],
    )
    chunks = FixedWindowChunker().chunk([page], _profile())
    tables = [c for c in chunks if c.chunk_type == "table"]
    assert len(tables) == 1
    t = tables[0]
    assert t.chunk_id == "doc1:p3:t0"
    assert "| Revenue | 1,000 | 900 |" in t.text
    assert t.text.splitlines()[1] == "Group results"


def test_long_tables_split_by_rows_with_header_repeated():
    rows = [["metric", "value"]] + [[f"row {i}", str(i)] for i in range(300)]
    chunks = FixedWindowChunker(ChunkingOptions(chunk_chars=200)).chunk(
        [PageText(page=1, text="t", tables=[rows])], _profile()
    )
    tables = [c for c in chunks if c.chunk_type == "table"]
    assert len(tables) > 1
    assert all("| metric | value |" in c.text for c in tables)


def test_table_to_markdown_pads_ragged_rows():
    md = table_to_markdown([["a", "b", "c"], ["1"]])
    assert md.splitlines()[2] == "| 1 |  |  |"


def test_table_to_markdown_drops_empty_columns():
    md = table_to_markdown([["Revenue", "", "2023", "", "2022"], ["Total", "", "1,000", "", "900"]])
    assert md.splitlines()[0] == "| Revenue | 2023 | 2022 |"
    assert md.splitlines()[2] == "| Total | 1,000 | 900 |"
