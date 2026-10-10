"""Extractor logic without an LLM: validation/mapping of items and page selection."""

from core.config import FactsConfig
from core.facts.extractor import ExtractedItem, select_pages, to_fact_records
from core.facts.registry import load_registry
from core.models import Chunk, DocumentProfile, RetrievedChunk


def _profile() -> DocumentProfile:
    return DocumentProfile(
        document_id="doc",
        source_path="x",
        file_name="x.pdf",
        doc_type="annual_report",
        issuer="Acme",
        legal_entity="Acme plc",
        entity_type="group",
        is_primary_issuer_report=True,
        fiscal_year=2023,
        reporting_currency="EUR",
        reporting_unit="millions",
    )


def test_to_fact_records_maps_units_currency_and_comparatives():
    items = [
        ExtractedItem(
            metric_alias_seen="Group revenues",
            canonical_metric="revenue",
            fiscal_year=2023,
            value=111239,
        ),
        ExtractedItem(
            metric_alias_seen="Group revenues",
            canonical_metric="revenue",
            fiscal_year=2022,
            value=98990,
            is_comparative=True,
        ),
        ExtractedItem(
            metric_alias_seen="Net result",
            canonical_metric="net result",
            fiscal_year=2023,
            value=-112654,
            unit="thousands",
        ),
        ExtractedItem(
            metric_alias_seen="Weird", canonical_metric="unknown", fiscal_year=2023, value=1
        ),
        ExtractedItem(
            metric_alias_seen="Revenue", canonical_metric="revenue", fiscal_year=1800, value=1
        ),
    ]
    records, warnings = to_fact_records(
        items, _profile(), 10, load_registry(), model="m", prompt_version="1"
    )
    assert len(records) == 3 and len(warnings) == 2
    r23, r22, nr = records
    assert r23.value_base == 111239e6 and r23.currency == "EUR" and not r23.is_comparative
    assert r22.is_comparative and r22.source_fiscal_year == 2023
    assert (
        nr.metric == "net_income" and nr.value_base == -112654e3 and nr.unit_reported == "thousands"
    )
    assert len({r.fact_id for r in records}) == 3


class _FakeRetriever:
    """Returns page votes: hints mentioning 'revenue' hit page 10, 'net' hits page 12, else page 3."""

    def search(self, query, filters=None, k=None, issuers=None):
        page = 10 if "revenue" in query.lower() else 12 if "net" in query.lower() else 3
        chunk = Chunk(
            chunk_id=f"doc:p{page}:c0",
            document_id="doc",
            page=page,
            chunk_type="text",
            text="t",
            content_hash="h",
        )
        return [RetrievedChunk(chunk=chunk, rrf_score=1.0)]


def test_select_pages_votes_then_adds_alias_tables():
    chunks = [
        Chunk(
            chunk_id="doc:p40:t0",
            document_id="doc",
            page=40,
            chunk_type="table",
            text="| Total revenues | 1 |",
            content_hash="h",
        ),
        Chunk(
            chunk_id="doc:p41:t0",
            document_id="doc",
            page=41,
            chunk_type="table",
            text="| Employees | 1 |",
            content_hash="h",
        ),
    ]
    pages = select_pages(
        _profile(),
        chunks,
        load_registry(),
        _FakeRetriever(),
        FactsConfig(pages_per_document=2, max_table_pages=2),
    )
    assert 10 in pages and 40 in pages and 41 not in pages
    assert len(pages) <= 4
