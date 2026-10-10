"""Hybrid retrieval: dense + BM25 fused with reciprocal rank fusion, plus page expansion."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from core.config import RetrievalConfig
from core.index.embeddings import EmbeddingModel
from core.index.lexical import LexicalIndex
from core.index.vector_store import VectorStore
from core.ingestion.store import DocumentStore
from core.models import Chunk, RetrievedChunk

log = logging.getLogger(__name__)

_NUMERIC = re.compile(r"(?<![A-Za-z])[-(]?\d[\d,.]*\)?")


def reciprocal_rank_fusion(
    rankings: Iterable[list[str]], k: int = 60
) -> dict[str, tuple[float, list[int | None]]]:
    """Fuse ranked id lists: ``score = Σ 1/(k + rank)`` over the lists containing the id.

    Returns ``{id: (score, [rank_in_list_0, rank_in_list_1, ...])}`` with 1-based ranks
    (``None`` when absent from a list).
    """
    rankings = list(rankings)
    scores: dict[str, float] = {}
    ranks: dict[str, list[int | None]] = {}
    for list_index, ranking in enumerate(rankings):
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(cid, [None] * len(rankings))[list_index] = rank
    return {cid: (scores[cid], ranks[cid]) for cid in scores}


def count_numeric_tokens(text: str) -> int:
    """How many number-like tokens a text contains (used to decide page expansion)."""
    return len(_NUMERIC.findall(text))


class HybridRetriever:
    """Dense + lexical retrieval with RRF, per-issuer round robin and page expansion."""

    def __init__(
        self,
        embedding: EmbeddingModel,
        vector_store: VectorStore,
        lexical: LexicalIndex,
        store: DocumentStore,
        config: RetrievalConfig,
    ) -> None:
        self.embedding = embedding
        self.vector_store = vector_store
        self.lexical = lexical
        self.store = store
        self.config = config

    # ------------------------------------------------------------------ public

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        k: int | None = None,
        issuers: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve the top ``k`` chunks for ``query``.

        ``issuers`` with more than one entry triggers one pass per issuer merged round-robin
        so every issuer is represented. Fiscal year is never applied as a hard filter by the
        retriever itself — callers may pass it in ``filters`` knowingly.
        """
        top_k = k or self.config.top_k
        base_filters = dict(filters or {})
        if issuers and len(issuers) > 1 and self.config.per_issuer_round_robin:
            per_issuer = [
                self._single_pass(query, {**base_filters, "issuer": issuer}, top_k)
                for issuer in issuers
            ]
            merged = _round_robin(per_issuer, top_k)
        else:
            if issuers:
                base_filters["issuer"] = issuers[0]
            merged = self._single_pass(query, base_filters, top_k)
        if self.config.page_expansion:
            self._expand_pages(merged)
        return merged

    # ------------------------------------------------------------------ internals

    def _single_pass(self, query: str, filters: dict[str, Any], top_k: int) -> list[RetrievedChunk]:
        n = self.config.candidates_per_index
        dense = self.vector_store.query(self.embedding.embed_query(query), n, filters or None)
        lexical = self.lexical.query(query, n, filters or None)
        fused = reciprocal_rank_fusion(
            [[cid for cid, _ in dense], [cid for cid, _ in lexical]], self.config.rrf_k
        )
        ordered = sorted(fused.items(), key=lambda item: item[1][0], reverse=True)[:top_k]
        chunks = {c.chunk_id: c for c in self._fetch([cid for cid, _ in ordered])}
        results: list[RetrievedChunk] = []
        for cid, (score, (dense_rank, lexical_rank)) in ordered:
            chunk = chunks.get(cid)
            if chunk is None:
                continue
            results.append(
                RetrievedChunk(
                    chunk=chunk, dense_rank=dense_rank, lexical_rank=lexical_rank, rrf_score=score
                )
            )
        return results

    def _fetch(self, chunk_ids: list[str]) -> list[Chunk]:
        found = {c.chunk_id: c for c in self.lexical.get(chunk_ids)}
        missing = [cid for cid in chunk_ids if cid not in found]
        if missing:
            for chunk in self.vector_store.get(missing):
                found[chunk.chunk_id] = chunk
        return [found[cid] for cid in chunk_ids if cid in found]

    def _expand_pages(self, results: list[RetrievedChunk]) -> None:
        seen: set[tuple[str, int]] = set()
        for result in results:
            chunk = result.chunk
            needs = (
                chunk.chunk_type == "table"
                or count_numeric_tokens(chunk.text) >= self.config.page_expansion_min_numeric_tokens
            )
            if not needs:
                continue
            key = (chunk.document_id, chunk.page)
            if key in seen:
                continue  # one expansion per page; later hits keep their own chunk text
            seen.add(key)
            try:
                page_text = self.store.page_text(chunk.document_id, chunk.page)
            except FileNotFoundError:
                page_text = None
            if page_text:
                header = chunk.text.split("\n", 1)[0]
                result.expanded_text = (
                    f"{header}\n{page_text[: self.config.page_expansion_max_chars]}"
                )


def _round_robin(lists: list[list[RetrievedChunk]], limit: int) -> list[RetrievedChunk]:
    out: list[RetrievedChunk] = []
    seen: set[str] = set()
    index = 0
    while len(out) < limit and any(index < len(lst) for lst in lists):
        for lst in lists:
            if index < len(lst) and lst[index].chunk.chunk_id not in seen:
                out.append(lst[index])
                seen.add(lst[index].chunk.chunk_id)
                if len(out) >= limit:
                    break
        index += 1
    return out
