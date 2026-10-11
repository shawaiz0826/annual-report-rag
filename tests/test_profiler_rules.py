"""Profiler helpers: unit-declaration detection, page selection, validation rules (no LLM)."""

import pytest

from core.config import IngestionConfig
from core.ingestion.profiler import DocumentProfiler, detect_unit_declarations, select_profile_pages
from core.models import DocumentProfile, PageText


def _pages() -> list[PageText]:
    filler = "Narrative text about strategy and markets. " * 10
    pages = [PageText(page=i, text=f"Cover or narrative {i}\n{filler}") for i in range(1, 9)]
    pages[5] = PageText(
        page=6, text="Income statement\nin euro thousand Note 2023 2022\nInterest income 1,000 900"
    )
    pages[6] = PageText(page=7, text="Balance sheet in euro thousand 2023 2022\nAssets 5,000 4,000")
    return pages


def test_detect_unit_declarations_counts_and_examples():
    found = detect_unit_declarations(_pages())
    assert found[0][0] == "thousands" and found[0][1] == 2 and found[0][3] == 6
    assert (
        detect_unit_declarations([PageText(page=1, text="(in millions, except per share data)")])[
            0
        ][0]
        == "millions"
    )
    assert (
        detect_unit_declarations([PageText(page=1, text="in € million 2021 2020")])[0][0]
        == "millions"
    )
    assert detect_unit_declarations([PageText(page=1, text="no units here")]) == []


def test_select_profile_pages_adds_middle_and_first_statements_page():
    chosen = select_profile_pages(_pages(), IngestionConfig(profile_pages=2))
    assert [p.page for p in chosen] == [1, 2, 5, 6]


def test_validate_overrides_undeclared_unit_and_fixes_flags():
    profile = DocumentProfile(
        document_id="d",
        source_path="x",
        file_name="x.pdf",
        doc_type="annual_report",
        issuer="Acme",
        legal_entity="Acme Finance B.V.",
        entity_type="subsidiary",
        is_primary_issuer_report=True,
        fiscal_year=2023,
        reporting_currency="EUR",
        reporting_unit="millions",
        profiler_confidence=0.9,
    )
    out = DocumentProfiler._validate(profile, _pages())
    assert out.reporting_unit == "thousands"
    assert out.is_primary_issuer_report is False
    assert out.profiler_confidence < 0.9 and len(out.warnings) == 2


def test_validate_clears_entity_fields_for_news():
    profile = DocumentProfile(
        document_id="d",
        source_path="x",
        file_name="n.pdf",
        doc_type="news",
        issuer="Acme",
        legal_entity="Acme plc",
    )
    out = DocumentProfiler._validate(profile, [PageText(page=1, text="news")])
    assert out.issuer is None and out.legal_entity is None and out.warnings


def test_source_paths_are_portable(tmp_path, monkeypatch):
    """The committed catalog must work on any machine: relative paths, name fallback."""
    from core.config import ROOT_DIR
    from core.ingestion.profiler import portable_path, resolve_source_path

    inside = ROOT_DIR / "data" / "raw" / "x.pdf"
    assert portable_path(inside) == "data/raw/x.pdf"
    outside = tmp_path / "y.pdf"
    assert portable_path(outside) == str(outside.resolve())

    raw = tmp_path / "raw" / "Acme"
    raw.mkdir(parents=True)
    (raw / "report.pdf").write_bytes(b"%PDF")
    found = resolve_source_path(
        "/elsewhere/build/data/raw/Acme/report.pdf", "report.pdf", tmp_path / "raw"
    )
    assert found == raw / "report.pdf"
    with pytest.raises(FileNotFoundError):
        resolve_source_path("/elsewhere/missing.pdf", "missing.pdf", tmp_path / "raw")
