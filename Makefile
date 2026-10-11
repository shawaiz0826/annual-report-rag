.DEFAULT_GOAL := help
PY ?= python3.11
VENV ?= .venv
BIN := $(VENV)/bin
PYTHON := $(BIN)/python
TORCH_INDEX := https://download.pytorch.org/whl/cpu

.PHONY: help setup lint format test test-all ingest reingest reprocess rebuild import-facts reindex chat catalog facts search extract-facts api ui eval readme docker-build docker-up docker-down clean-index

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Create .venv (Python 3.11), install CPU torch then all pinned deps
	test -d $(VENV) || $(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install torch==2.14.1 --index-url $(TORCH_INDEX)
	$(BIN)/pip install -r requirements-dev.txt
	test -f .env || cp .env.example .env
	@echo "Done. Put your OPENROUTER_API_KEY in .env"

lint: ## Ruff lint + format check
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Auto-format with ruff
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

test: ## Unit tests (no network)
	$(BIN)/pytest -m "not integration"

test-all: ## Unit + integration tests (needs .env and network)
	$(BIN)/pytest

ingest: ## Ingest data/raw (idempotent; skips documents already in the catalog)
	$(PYTHON) scripts/ingest.py

reingest: ## Force re-ingestion of every document
	$(PYTHON) scripts/ingest.py --force

reprocess: ## Re-clean/re-chunk/re-index from source files, keeping profiles (no LLM calls)
	$(PYTHON) -m apps.cli reprocess

rebuild: ## Rebuild ALL local state from committed artefacts with ZERO model calls (≈5–15 min on CPU)
	$(PYTHON) -m apps.cli reprocess
	$(PYTHON) -m apps.cli import-facts

import-facts: ## Load facts from data/derived/facts_export.csv (no LLM calls)
	$(PYTHON) -m apps.cli import-facts

reindex: ## Rebuild vector + BM25 indexes from stored chunks (no LLM calls)
	$(PYTHON) -m apps.cli reindex

chat: ## Terminal chat
	$(PYTHON) -m apps.cli chat

catalog: ## Print the discovered document catalog
	$(PYTHON) -m apps.cli catalog

facts: ## Print facts: make facts ARGS="Tesla revenue"
	$(PYTHON) -m apps.cli facts $(ARGS)

extract-facts: ## (Re)run fact extraction + reconciliation without re-profiling
	$(PYTHON) -m apps.cli extract-facts

search: ## Hybrid search: make search Q="BMW group revenues 2017" ARGS="--issuer BMW"
	$(PYTHON) scripts/search.py "$(Q)" $(ARGS)

api: ## Run the FastAPI service on :8000
	$(BIN)/uvicorn api.main:app --host 0.0.0.0 --port 8000

ui: ## Run the Streamlit UI on :8501
	$(BIN)/streamlit run apps/streamlit_app.py --server.port 8501 --server.headless true

eval: ## Run the golden-set eval on both configured models and write eval/reports/latest.md
	$(PYTHON) -m eval.run_eval --both

readme: ## Fill the README evaluation section from the latest eval report
	$(PYTHON) scripts/render_readme.py

docker-build: ## Build the Docker image
	docker compose build

docker-up: ## Start API + UI with docker compose
	docker compose up -d

docker-down: ## Stop docker compose services
	docker compose down

clean-index: ## Delete local indexes, facts DB, sessions and traces
	rm -rf data/index
