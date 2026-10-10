"""Index building blocks without models or network: tokeniser, filters, RRF, round robin, Chroma where."""

from core.index.hybrid import _round_robin, count_numeric_tokens, reciprocal_rank_fusion
from core.index.lexical import matches_filters, tokenize
from core.index.vector_store import to_chroma_where
from core.models import Chunk, RetrievedChunk


def test_tokenize_numbers_both_forms_and_short_tokens():
    tokens = tokenize("Total revenues 96,773 and 1,234.5 in FY 2023; a b")
    assert "revenues" in tokens and "2023" in tokens and "fy" in tokens
    assert "96773" in tokens and "96" in tokens and "773" in tokens
    assert "1234.5" in tokens
    assert "a" not in tokens and "b" not in tokens


def test_matches_filters_scalar_and_list():
    meta = {"issuer": "Acme", "fiscal_year": 2023, "doc_type": "annual_report"}
    assert matches_filters(meta, None)
    assert matches_filters(meta, {"issuer": "Acme"})
    assert matches_filters(meta, {"fiscal_year": [2022, 2023]})
    assert not matches_filters(meta, {"issuer": "Other"})
    assert not matches_filters(meta, {"doc_type": ["news"]})
    assert matches_filters(meta, {"issuer": None, "fiscal_year": []})


def test_rrf_scores_and_ranks():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]], k=60)
    assert fused["b"][0] > fused["a"][0] > fused["d"][0]
    assert fused["b"][1] == [2, 1]
    assert fused["a"][1] == [1, None]
    assert abs(fused["b"][0] - (1 / 62 + 1 / 61)) < 1e-12


def test_chroma_where_translation():
    assert to_chroma_where(None) is None
    assert to_chroma_where({"issuer": "Acme"}) == {"issuer": "Acme"}
    assert to_chroma_where({"issuer": ["A", "B"]}) == {"issuer": {"$in": ["A", "B"]}}
    assert to_chroma_where({"issuer": "A", "doc_type": ["x"], "skip": None}) == {
        "$and": [{"issuer": "A"}, {"doc_type": "x"}]
    }


def test_numeric_token_count():
    assert count_numeric_tokens("Revenue 96,773 (1,981) 12.5% in 2023 vs 2022") == 5
    assert count_numeric_tokens("no numbers here") == 0


def _rc(cid: str) -> RetrievedChunk:
    chunk = Chunk(
        chunk_id=cid, document_id="d", page=1, chunk_type="text", text=cid, content_hash="h"
    )
    return RetrievedChunk(chunk=chunk, rrf_score=1.0)


def test_round_robin_alternates_and_dedupes():
    a = [_rc("a1"), _rc("a2"), _rc("a3")]
    b = [_rc("b1"), _rc("a2")]
    out = _round_robin([a, b], limit=5)
    assert [r.chunk.chunk_id for r in out] == ["a1", "b1", "a2", "a3"]
