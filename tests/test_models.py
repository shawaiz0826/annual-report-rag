"""Model helpers: provenance header, usage arithmetic, citation labels."""

from core.models import Citation, DocumentProfile, Usage


def _profile(**kw) -> DocumentProfile:
    base = dict(
        document_id="abc",
        source_path="x.pdf",
        file_name="x.pdf",
        doc_type="annual_report",
        legal_entity="Example Finance N.V.",
        fiscal_year=2023,
        reporting_currency="EUR",
        reporting_unit="thousands",
    )
    base.update(kw)
    return DocumentProfile(**base)


def test_provenance_header_full():
    assert (
        _profile().provenance_header(12)
        == "[Example Finance N.V. | annual report FY2023 | EUR thousands | p.12]"
    )


def test_provenance_header_omits_missing():
    p = _profile(
        legal_entity=None,
        reporting_currency=None,
        reporting_unit=None,
        fiscal_year=None,
        doc_type="news",
    )
    assert p.provenance_header(1) == "[news | p.1]"


def test_usage_addition_handles_none_cost():
    a = Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3, cost_usd=None)
    b = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30, cost_usd=0.5)
    c = a + b
    assert c.total_tokens == 33 and c.cost_usd == 0.5
    assert (a + a).cost_usd is None


def test_citation_label():
    c = Citation(
        document_id="d",
        file_name="f.pdf",
        legal_entity="Tesla, Inc.",
        fiscal_year=2023,
        page=5,
        kind="fact",
        ref="r",
    )
    assert c.label() == "[Tesla, Inc. FY2023 p.5]"
