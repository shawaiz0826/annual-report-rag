"""Integration tests — need .env with a working key and network. Run with `make test-all`."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import IngestionConfig, get_settings
from core.ingestion.profiler import DocumentProfiler
from core.llm.client import build_provider
from core.models import LLMResponse, PageText

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def provider():
    settings = get_settings()
    if not settings.openrouter_api_key.get_secret_value():
        pytest.skip("no OPENROUTER_API_KEY")
    return build_provider(settings)


def test_tool_calling_smoke(provider):
    response: LLMResponse = provider.chat(
        [{"role": "user", "content": "Look up Acme revenue for 2023 using the tool."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup_facts",
                    "description": "Look up a financial fact",
                    "parameters": {
                        "type": "object",
                        "properties": {"issuer": {"type": "string"}, "metric": {"type": "string"}},
                        "required": ["issuer", "metric"],
                    },
                },
            }
        ],
        max_tokens=2000,
    )
    assert response.tool_calls, "model did not call the tool"
    call = response.tool_calls[0]
    assert call.name == "lookup_facts" and call.arguments.get("issuer", "").lower().startswith(
        "acme"
    )
    assert response.usage.total_tokens > 0


def test_profiler_identifies_a_financing_subsidiary(provider, tmp_path: Path):
    """Synthetic pages in the style of a financing subsidiary's report (no dataset dependency)."""
    pages = [
        PageText(page=1, text="ANNUAL REPORT 2023\nACME FINANCE B.V.\nACME GROUP"),
        PageText(
            page=2,
            text=(
                "Annual Management Report\nAcme Finance B.V. (the Company) was founded as a wholly owned "
                "subsidiary of Acme Holding B.V., which in turn is a wholly owned subsidiary of Acme AG. The "
                "purpose of the Company is to assist in the financing of business activities conducted by "
                "companies of the Acme Group. The Company is incorporated in the Netherlands."
            ),
        ),
        PageText(
            page=3,
            text=(
                "Income Statement for the year ended 31 December 2023\nin euro thousand Note 2023 2022\n"
                "Interest income Acme Group companies 1,537,821 739,839\nInterest expenses (1,939,115) (961,574)\n"
                "Income/(loss) before taxation (506,962) 550,660\nNet income / (loss) (394,288) 403,943"
            ),
        ),
    ]
    profiler = DocumentProfiler(
        provider, IngestionConfig(profile_pages=3, profile_sample_middle=False)
    )
    profile, usage = profiler.profile(
        tmp_path / "Acme_Annual_Report_2023.pdf", "deadbeefdeadbeef", pages, 3
    )
    assert profile.doc_type in ("annual_report", "financial_statements")
    assert profile.entity_type == "subsidiary" and profile.is_primary_issuer_report is False
    assert profile.issuer and "acme" in profile.issuer.lower()
    assert profile.legal_entity and "finance" in profile.legal_entity.lower()
    assert profile.fiscal_year == 2023 and profile.reporting_currency == "EUR"
    assert profile.reporting_unit == "thousands"
    assert usage.total_tokens > 0
