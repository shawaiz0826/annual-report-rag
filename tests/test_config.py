"""Config loading: YAML defaults, env override, path resolution."""

from pathlib import Path

from core.config import ROOT_DIR, Settings, get_settings


def test_yaml_defaults_loaded():
    s = get_settings()
    assert s.chunking.chunk_chars == 800
    assert s.retrieval.rrf_k == 60
    assert s.embeddings.model.startswith("BAAI/")


def test_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("RETRIEVAL__TOP_K", "3")
    monkeypatch.setenv("LLM_MODEL", "test/model")
    s = Settings()
    assert s.retrieval.top_k == 3
    assert s.llm_model == "test/model"


def test_paths_are_absolute_under_root():
    s = get_settings()
    assert s.paths.raw_dir.is_absolute()
    assert s.paths.raw_dir == ROOT_DIR / "data" / "raw"
    assert isinstance(s.sessions.path, Path)


def test_api_key_is_redacted():
    s = Settings(openrouter_api_key="sk-secret")
    assert "sk-secret" not in repr(s)
    assert s.redacted()["openrouter_api_key"] == "***"


def test_models_for_eval_returns_two():
    s = get_settings()
    assert len(s.models_for_eval()) == 2
