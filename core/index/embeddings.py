"""Embedding model protocol and the sentence-transformers implementation (CPU)."""

from __future__ import annotations

import logging
from typing import Protocol

from core.config import EmbeddingsConfig

log = logging.getLogger(__name__)


class EmbeddingModel(Protocol):
    """Dense text encoder."""

    name: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode passages."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Encode a query (may apply an instruction prefix)."""
        ...


class SentenceTransformersEmbedding:
    """``sentence-transformers`` model on CPU with normalised outputs."""

    def __init__(self, config: EmbeddingsConfig) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, keep local

        self.name = config.model
        self.batch_size = config.batch_size
        self.normalize = config.normalize
        self.query_prefix = config.query_prefix
        log.info("loading embedding model %s", config.model)
        self._model = SentenceTransformer(config.model, device="cpu")
        self.dimension = int(self._model.get_sentence_embedding_dimension() or 0)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode passages in batches."""
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Encode one query with the configured instruction prefix."""
        vector = self._model.encode(
            [self.query_prefix + text],
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )[0]
        return vector.tolist()


def build_embedding_model(config: EmbeddingsConfig) -> EmbeddingModel:
    """Factory keyed on ``config.provider``."""
    if config.provider == "sentence_transformers":
        return SentenceTransformersEmbedding(config)
    raise ValueError(f"unknown embeddings provider {config.provider!r}")
