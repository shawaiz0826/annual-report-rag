"""Application settings.

Precedence (highest first): explicit kwargs → environment variables → `.env` →
`config.yaml` → model defaults. Nested YAML sections can be overridden from the
environment with a double underscore, e.g. ``RETRIEVAL__TOP_K=5``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
"""Repository root; relative paths in config are resolved against it."""


class PathsConfig(BaseModel):
    """Filesystem locations."""

    raw_dir: Path = Path("data/raw")
    derived_dir: Path = Path("data/derived")
    index_dir: Path = Path("data/index")
    traces_dir: Path = Path("data/index/traces")
    replay_cache: Path = Path("data/index/replay.json")

    @field_validator("*", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT_DIR / value


class IngestionConfig(BaseModel):
    """Document loading, profiling and cleaning."""

    profile_pages: int = 3
    profile_sample_middle: bool = True
    profile_max_chars_per_page: int = 6000
    dedupe_lines: bool = True
    header_footer_min_page_ratio: float = 0.5
    header_footer_long_line_ratio: float = 0.15
    header_footer_long_line_chars: int = 25
    header_footer_zone_lines: int = 3
    min_chunk_chars: int = 200
    min_pages_for_header_detection: int = 4


class ChunkingConfig(BaseModel):
    """Chunking strategy parameters."""

    strategy: Literal["fixed_window"] = "fixed_window"
    chunk_chars: int = 800
    overlap_chars: int = 100
    respect_page_boundary: bool = True
    table_chunks: bool = True
    min_table_rows: int = 2


class EmbeddingsConfig(BaseModel):
    """Embedding model parameters."""

    provider: Literal["sentence_transformers"] = "sentence_transformers"
    model: str = "BAAI/bge-small-en-v1.5"
    batch_size: int = 64
    normalize: bool = True
    query_prefix: str = "Represent this sentence for searching relevant passages: "


class VectorStoreConfig(BaseModel):
    """Vector store parameters."""

    provider: Literal["chroma"] = "chroma"
    collection: str = "chunks"


class LexicalConfig(BaseModel):
    """BM25 parameters."""

    provider: Literal["bm25"] = "bm25"
    k1: float = 1.5
    b: float = 0.75


class RetrievalConfig(BaseModel):
    """Hybrid retrieval parameters."""

    top_k: int = 8
    candidates_per_index: int = 30
    rrf_k: int = 60
    per_issuer_round_robin: bool = True
    page_expansion: bool = True
    page_expansion_max_chars: int = 4000
    page_expansion_min_numeric_tokens: int = 8


class FactsConfig(BaseModel):
    """Fact extraction and reconciliation parameters."""

    pages_per_document: int = 6
    max_table_pages: int = 4
    extraction_concurrency: int = 4
    agreement_tolerance_pct: float = 1.0
    min_confidence_to_use: float = 0.5


class AgentConfig(BaseModel):
    """Agent loop parameters."""

    max_steps: int = 6
    temperature: float = 0.0
    max_history_turns: int = 4
    history_answer_chars: int = 300
    show_model_knowledge: bool = False
    tool_result_max_chars: int = 6000
    max_output_tokens: int = 4096


class ModelPrice(BaseModel):
    """USD per 1M tokens."""

    input: float = 0.0
    output: float = 0.0


class LLMConfig(BaseModel):
    """LLM client behaviour (model identifiers come from the environment)."""

    timeout_s: float = 90.0
    max_retries: int = 3
    backoff_base_s: float = 1.5
    exclude_reasoning: bool = True
    usage_accounting: Literal["openrouter", "price_table"] = "openrouter"
    price_table: dict[str, ModelPrice] = Field(default_factory=dict)
    known_models: list[str] = Field(default_factory=list)


class SessionsConfig(BaseModel):
    """Session store parameters."""

    backend: Literal["sqlite", "memory"] = "sqlite"
    path: Path = Path("data/index/sessions.db")

    @field_validator("path", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT_DIR / value


class ApiConfig(BaseModel):
    """FastAPI bind address."""

    host: str = "0.0.0.0"
    port: int = 8000


class Settings(BaseSettings):
    """Top-level settings object. Use :func:`get_settings` or the module-level ``settings``."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        yaml_file=os.environ.get("CONFIG_PATH", str(ROOT_DIR / "config.yaml")),
        extra="ignore",
        case_sensitive=False,
    )

    # --- secrets and model identifiers (environment / .env) ---
    openrouter_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    llm_model_fallback: str = "google/gemma-4-31b-it:free"
    llm_model_dev: str | None = None
    llm_http_referer: str = "https://github.com/shawaiz0826/annual-report-rag"
    llm_app_title: str = "annual-report-rag"
    log_level: str = "INFO"

    # --- tunables (config.yaml) ---
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    lexical: LexicalConfig = Field(default_factory=LexicalConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    facts: FactsConfig = Field(default_factory=FactsConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)

    @field_validator("llm_model_dev", mode="before")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        return value or None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the YAML file below env/.env so environment always wins."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    def models_for_eval(self) -> list[str]:
        """The two models `make eval` compares."""
        return [self.llm_model, self.llm_model_fallback]

    def redacted(self) -> dict:
        """Settings as a dict with the API key masked (safe for logs and traces)."""
        data = self.model_dump(mode="json")
        data["openrouter_api_key"] = "***" if self.openrouter_api_key.get_secret_value() else ""
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()
