# Annual-Report Analyst — Execution Plan

**Audience:** Claude Code (implementer). **Owner:** Shawaiz Ahmed Bajwa.
**Purpose:** Build a production-shaped, scalable Retrieval-Augmented Generation (RAG) system that answers analyst questions over automotive annual reports, for the AECOM AI Engineer take-home.

> How to use this document with Claude Code
> 1. Create the repo folder and copy this file to `docs/EXECUTION_PLAN.md`.
> 2. Start with: *"Read docs/EXECUTION_PLAN.md fully. Implement milestone M0, run its acceptance checks, commit, then stop and report."*
> 3. Proceed milestone by milestone (M0 → M7). Do not skip acceptance checks. Do not start a milestone before the previous one's checks pass.
> 4. Anything marked **DECISION** is settled — implement as written. Anything marked **VERIFY** must be confirmed against the source PDFs before use.

---

## 0. Table of contents

1. Context and the brief
2. Data audit (what we found in the dataset)
3. Engineering principles (non-negotiable)
4. Tech stack and versions
5. Repository layout
6. Configuration
7. Data models
8. Ingestion pipeline
9. Document profiling
10. Cleaning
11. Chunking
12. Indexing: embeddings, vector store, lexical index, catalog
13. Hybrid retrieval
14. Facts layer: metric registry, extraction, reconciliation, store, compute
15. LLM client
16. Agent: tools, loop, prompts, grounding rules
17. Sessions and memory
18. Observability and tracing
19. Service layer and API
20. CLI
21. Streamlit UI
22. Evaluation harness and golden set
23. Replay cache
24. Tests
25. Docker
26. Documentation deliverables (README, DATA_AUDIT, DECISIONS)
27. Milestones with acceptance criteria
28. Demo script
29. Definition of done
30. Explicitly out of scope

---

## 1. Context and the brief

**Persona:** a financial analyst who reads automotive annual reports daily, extracts revenue / profit / growth metrics, and compares companies (and sectors).

**Ask:** a chatbot-like RAG application over the provided reports that answers questions such as:

- What was BMW's total revenue in 2023?
- How much revenue did Tesla generate in 2023?
- What was Ford's revenue for the year 2020?
- Can you provide the revenue figures for BMW in 2017?
- What key economic factors influenced Ford's performance in 2021?
- Which Tesla product is currently in the development stage?
- What were BMW's profit figures for 2020 and 2023?
- Between Tesla and Ford, which company achieved higher profits in 2022?
- What were Tesla's profit numbers for 2022 and 2023?
- Which company recorded better profitability in 2022 overall?
- Provide a summary of revenue figures for Tesla, BMW, and Ford over the past three years.
- What were the growth trends for BMW's financial performance from 2020 to 2023?

**Hard requirements:** follow-up questions must work; deliver either a Dockerised web app *or* a terminal demo; share a **private** GitHub repo ≥ 2 hours before the interview; demo live; discuss design, LLMs, and typical RAG challenges.

**Our delivery (DECISION):** terminal CLI is the primary demo; FastAPI service + Streamlit + Docker are included because the architecture is service-based anyway.

---

## 2. Data audit (what we found in the dataset)

Dataset location on the owner's machine: `/Users/shezzy/Downloads/AI Engineer/Data/`

| File | Pages | What it actually is |
|---|---|---|
| `BMW/BMW_Annual_Report_2021.pdf` | 50 | **BMW Group Report 2021** (extract). Contains a five-year table with Group revenues and net profit for **2017–2021**. Currency EUR, unit € million. |
| `BMW/BMW_Annual_Report_2022.pdf` | 54 | **BMW Finance N.V. Annual Report 2022** — the Dutch financing subsidiary. No vehicle revenue; interest income, net result. Currency EUR. **Not** the Group report. |
| `BMW/BMW_Annual_Report_2023.pdf` | 55 | **BMW Finance N.V. Annual Report 2023** — same as above. |
| `Ford/Ford_Annual_Report_2021.pdf` | 75 | Ford 10-K extract, FY2021. Contains FY2020 comparatives. USD, $ million. |
| `Ford/Ford_Annual_Report_2022.pdf` | 76 | Ford 10-K extract, FY2022. |
| `Ford/Ford_Annual_Report_2023.pdf` | 75 | Ford 10-K extract, FY2023. **Every text line is duplicated** in the extraction layer (582k chars vs ~270k for other years). |
| `Tesla/Tesla_Annual_Report_2022.pdf` | 62 | Tesla 10-K extract, FY2022. Contains FY2021/2020 comparatives. USD, $ million. |
| `Tesla/Tesla_Annual_Report_2023.pdf` | 66 | Tesla 10-K extract, FY2023. |
| `news.pdf` / `news.docx` | 9 | **Unrelated news** (elections, football, a film, a central-bank rate rise). Deliberate noise. |

Consequences (all handled generically, never special-cased in code):

1. **Entity ≠ issuer.** "BMW 2023" resolves to BMW Finance N.V., not BMW Group. The system must discover and state the reporting entity.
2. **Year ≠ document year.** 2017–2020 figures exist inside later reports. Never hard-filter on year.
3. **Noise must be isolated.** `news.pdf` gets profiled as `doc_type=news`, `issuer=null`, and is excluded from issuer-filtered retrieval.
4. **Extraction must deduplicate** repeated lines and strip running headers/footers.
5. **Currencies differ** (EUR vs USD). Cross-currency absolute comparisons must be refused or caveated; ratio comparisons (margins, growth) are allowed.

---

## 3. Engineering principles (non-negotiable)

1. **Nothing about this dataset lives in code.** No company names, years, currencies, entity relationships, or file names in source. These are *discovered at ingest* (document profiling) or *declared in config* (metric registry). Adding a new issuer, year, or sector = add files, re-run ingest.
2. **Configuration over constants.** Every tunable (chunk size, overlap, top-k, RRF k, model IDs, step caps, timeouts, paths, prices) lives in `config.yaml` or environment variables, loaded via `pydantic-settings`.
3. **Interfaces over implementations.** `EmbeddingModel`, `VectorStore`, `LexicalIndex`, `LLMProvider`, `SessionStore`, `DocumentLoader`, `ChunkingStrategy` are Protocols with one concrete implementation each. No second implementations are built; the seams are what matter.
4. **Provenance everywhere.** Every chunk, fact, tool result, and answer carries `document_id`, `page`, and (for facts) `confidence`. No answer without citations.
5. **Grounding is enforced, not hoped for.** Answers come only from tool results. Model memory is off by default and, when enabled by flag, is visibly labelled.
6. **Arithmetic happens in Python.** Growth, margins, differences are computed by the `compute_metric` tool, never by the LLM.
7. **Everything is traced; evals read traces.** One trace schema feeds debugging (`/why`), cost reporting, and the eval harness.
8. **No LangChain / LlamaIndex.** Hand-written pipeline; the owner must be able to explain every line.
9. **Incremental and idempotent ingestion** keyed on content hash.
10. **Small, meaningful commits** per milestone. Never commit secrets, indexes, or caches.
11. **Type hints everywhere, Pydantic models at every boundary, docstrings on public functions, `ruff` clean, tests pass.**

---

## 4. Tech stack and versions

- Python **3.11** (pin in `pyproject.toml` / `.python-version`)
- `pdfplumber` — PDF text + tables
- `python-docx` — DOCX loader
- `sentence-transformers` + `torch` (CPU) — embeddings, default model `BAAI/bge-small-en-v1.5`
- `chromadb` — persistent local vector store
- `rank-bm25` — lexical index
- `openai` — OpenAI-compatible client (OpenRouter / OpenAI / Ollama)
- `pydantic` v2, `pydantic-settings`
- `pyyaml`
- `simpleeval` — safe formula evaluation for derived metrics
- `fastapi`, `uvicorn` — service layer
- `rich`, `typer` — CLI
- `streamlit` — optional UI
- `pytest`, `ruff`
- SQLite via stdlib `sqlite3` — facts store, sessions (default backend)

Pin exact versions in `requirements.txt`; also keep `requirements-dev.txt`.

LLM access: **OpenRouter**, base URL `https://openrouter.ai/api/v1`. Models (verify exact slugs on openrouter.ai/models before use):
- primary: `anthropic/claude-sonnet-5` (**VERIFY slug**)
- secondary: `google/gemini-3.1-pro-preview` (**VERIFY slug**)
- development / cheap: a low-cost model chosen from the OpenRouter list (e.g. a Flash/Haiku-class model) — used for iteration, never for eval reports or the demo.

---

## 5. Repository layout

```
annual-report-rag/
├── README.md
├── docs/
│   ├── EXECUTION_PLAN.md          (this file)
│   ├── DATA_AUDIT.md
│   ├── DECISIONS.md               (decision log: choice / why / alternatives)
│   ├── ARCHITECTURE.md            (Mermaid diagrams + scaling section)
│   └── DEMO.md                    (demo script)
├── config.yaml
├── .env.example
├── .gitignore
├── pyproject.toml                 (ruff, pytest config)
├── requirements.txt
├── requirements-dev.txt
├── Makefile
├── Dockerfile
├── docker-compose.yml
├── data/
│   ├── raw/                       (the PDFs, committed; subfolders optional, ignored by loader logic)
│   ├── derived/                   (facts_export.csv, catalog.json — committed artefacts)
│   └── index/                     (chroma/, bm25.pkl, facts.db — gitignored)
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py                  (all Pydantic models)
│   ├── ingestion/
│   │   ├── loaders/ (base.py, pdf.py, docx.py, text.py, registry.py)
│   │   ├── profiler.py
│   │   ├── cleaner.py
│   │   ├── chunker.py
│   │   └── pipeline.py
│   ├── index/
│   │   ├── embeddings.py
│   │   ├── vector_store.py
│   │   ├── lexical.py
│   │   ├── hybrid.py
│   │   └── catalog.py
│   ├── facts/
│   │   ├── registry.yaml
│   │   ├── registry.py
│   │   ├── extractor.py
│   │   ├── reconcile.py
│   │   ├── store.py
│   │   └── compute.py
│   ├── agent/
│   │   ├── tools.py
│   │   ├── agent.py
│   │   ├── memory.py
│   │   └── prompts/ (system.md, profiler.md, extractor.md — versioned, with a `version:` header)
│   ├── llm/
│   │   └── client.py
│   ├── observability/
│   │   └── tracing.py
│   └── service.py                 (ChatService: the one entry point used by CLI, API, eval)
├── api/
│   └── main.py                    (FastAPI)
├── apps/
│   ├── cli.py
│   └── streamlit_app.py
├── eval/
│   ├── golden.json
│   ├── run_eval.py
│   ├── scorers.py
│   └── reports/ (report_<model>_<date>.md — committed)
├── scripts/
│   ├── ingest.py
│   ├── extract_facts.py
│   └── record_replay.py
└── tests/
```

---

## 6. Configuration

`core/config.py` defines `Settings(BaseSettings)` reading `.env` and `config.yaml` (YAML values are defaults; env vars override; CLI flags override env).

`.env.example`:
```
OPENROUTER_API_KEY=
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-sonnet-5
LLM_MODEL_DEV=
```

`config.yaml` (initial values — all tunable):
```yaml
paths:
  raw_dir: data/raw
  derived_dir: data/derived
  index_dir: data/index
  traces_dir: data/index/traces
  replay_cache: data/index/replay.json

ingestion:
  profile_pages: 3              # first N pages sent to the profiler
  profile_sample_middle: true   # also send one mid-document page
  dedupe_lines: true
  header_footer_min_page_ratio: 0.5   # a line repeating on >= this share of pages is a running header/footer
  min_chunk_chars: 200

chunking:
  strategy: fixed_window
  chunk_chars: 800
  overlap_chars: 100
  respect_page_boundary: true
  table_chunks: true            # emit extracted tables as separate chunks

embeddings:
  provider: sentence_transformers
  model: BAAI/bge-small-en-v1.5
  batch_size: 64
  normalize: true

vector_store:
  provider: chroma
  collection: chunks

lexical:
  provider: bm25
  k1: 1.5
  b: 0.75

retrieval:
  top_k: 8
  candidates_per_index: 30
  rrf_k: 60
  per_issuer_round_robin: true
  page_expansion: true
  page_expansion_max_chars: 4000

facts:
  pages_per_document: 6         # statement pages selected via retrieval per metric hints
  agreement_tolerance_pct: 1.0
  min_confidence_to_use: 0.5

agent:
  max_steps: 6
  temperature: 0.0
  max_history_turns: 4
  history_answer_chars: 300
  show_model_knowledge: false

llm:
  timeout_s: 60
  max_retries: 3
  backoff_base_s: 1.5
  usage_accounting: openrouter  # openrouter | price_table
  price_table:                  # USD per 1M tokens, fallback only; keep updated
    anthropic/claude-sonnet-5: {input: 0, output: 0}   # VERIFY
    google/gemini-3.1-pro-preview: {input: 0, output: 0}   # VERIFY

sessions:
  backend: sqlite
  path: data/index/sessions.db

api:
  host: 0.0.0.0
  port: 8000
```

---

## 7. Data models (`core/models.py`)

All Pydantic v2. These are the contracts between layers.

```python
class DocumentProfile(BaseModel):
    document_id: str  # sha256 of file bytes, first 16 hex
    source_path: str
    file_name: str
    doc_type: Literal["annual_report", "financial_statements", "news", "other"]
    issuer: str | None  # canonical brand / group name, e.g. "BMW"; None for news
    legal_entity: (
        str | None
    )  # e.g. "BMW Finance N.V.", "Tesla, Inc.", "Bayerische Motoren Werke AG"
    entity_type: Literal["group", "subsidiary", "other"] | None
    parent_organisation: str | None  # e.g. "BMW Group" for BMW Finance N.V.
    is_primary_issuer_report: bool  # True only for the group/parent's own report
    fiscal_year: int | None
    period_end: str | None  # ISO date if stated
    reporting_currency: str | None  # ISO 4217
    reporting_unit: Literal["units", "thousands", "millions", "billions"] | None
    sector: str | None  # e.g. "Automotive", "Financial services"
    country: str | None
    language: str
    page_count: int
    summary: str  # 1–2 sentences, what the document is
    profiler_confidence: float  # 0–1
    profiler_model: str
    profiler_prompt_version: str


class Chunk(BaseModel):
    chunk_id: str  # f"{document_id}:p{page}:c{n}" or ":t{n}" for tables
    document_id: str
    page: int  # 1-based
    chunk_type: Literal["text", "table"]
    text: str  # includes provenance header line
    metadata: dict  # flattened profile fields for filtering: issuer, legal_entity, entity_type, fiscal_year, doc_type, currency, sector
    content_hash: str


class RetrievedChunk(BaseModel):
    chunk: Chunk
    dense_rank: int | None
    lexical_rank: int | None
    rrf_score: float
    expanded_text: str | None  # page expansion if applied


class FactRecord(BaseModel):
    fact_id: str
    issuer: str
    legal_entity: str
    metric: str  # canonical name from registry
    fiscal_year: int
    value_reported: float
    unit_reported: str  # units|thousands|millions|billions
    currency: str
    value_base: float  # value in base units (reported * multiplier)
    document_id: str
    page: int
    source_fiscal_year: int  # the fiscal year of the document it came from
    is_comparative: bool  # True if fiscal_year != source_fiscal_year
    extraction_model: str
    extraction_prompt_version: str
    confidence: float  # set by reconcile
    reconciliation_note: (
        str | None
    )  # e.g. "agrees with FY2023 report", "restated: FY2022 report shows X"
    verified: bool = False  # set only by eval tooling, never by the app


class Citation(BaseModel):
    document_id: str
    file_name: str
    legal_entity: str | None
    fiscal_year: int | None
    page: int
    kind: Literal["chunk", "fact"]
    ref: str  # chunk_id or fact_id


class AgentAnswer(BaseModel):
    resolved_question: str  # the self-contained question the agent answered
    answered: bool  # False = information not in documents / out of scope
    answer_markdown: str
    citations: list[Citation]
    caveats: list[str]  # e.g. entity mismatch, currency mismatch, restatement
    confidence: Literal["high", "medium", "low"]
    model_knowledge_note: str | None  # only when show_model_knowledge=True
    entities_used: list[str]
    years_used: list[int]
    metrics_used: list[str]


class ToolCallRecord(BaseModel):
    step: int
    tool: str
    arguments: dict
    result_summary: str
    result_ref: str | None  # path/id of full result in trace
    latency_ms: int


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None


class Trace(BaseModel):
    trace_id: str
    session_id: str
    timestamp: str
    model: str
    prompt_versions: dict[str, str]
    user_message: str
    history_turns: int
    tool_calls: list[ToolCallRecord]
    retrieved: list[dict]  # chunk_id, page, document_id, dense_rank, lexical_rank, rrf_score
    final: AgentAnswer | None
    usage: Usage
    latency_ms: int
    error: str | None
    replayed: bool = False


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    answer: AgentAnswer | None = None
    trace_id: str | None = None


class Session(BaseModel):
    session_id: str
    created_at: str
    turns: list[Turn]
    slots: dict  # last entities/years/metrics for reference resolution
```

---

## 8. Ingestion pipeline (`core/ingestion/pipeline.py`)

Entry: `python scripts/ingest.py [--path data/raw] [--force]`.

Steps per file (recursively walk `raw_dir`; folder names are **not** used for metadata):

1. **Hash** file bytes → `document_id`. If present in the catalog and `--force` not set → skip (idempotent).
2. **Load** via `LoaderRegistry.get(extension)` → `list[PageText]` (`page`, `text`, `tables: list[list[list[str]]]`). PDF loader uses `pdfplumber`; DOCX loader treats the whole document as page 1 (or splits on page breaks if present); TXT loader likewise.
3. **Profile** (section 9) → `DocumentProfile`. Persist to catalog.
4. **Clean** (section 10).
5. **Chunk** (section 11) → `list[Chunk]`, each with the provenance header and flattened metadata.
6. **Index**: embed in batches → vector store upsert; add to lexical index; persist lexical index.
7. **Extract facts** (section 14) — only for `doc_type in {annual_report, financial_statements}`.
8. **Write catalog** `data/derived/catalog.json` and `data/derived/facts_export.csv`.
9. Print a summary table: file, entity, year, pages, chunks, facts, warnings.

Failures in one document must not abort the run; collect and report.

---

## 9. Document profiling (`core/ingestion/profiler.py`)

Input to the LLM: file name (weak hint only), page count, cleaned text of the first `profile_pages` pages, plus one mid-document page if configured. Output: `DocumentProfile` via JSON-schema-constrained response (use the OpenAI-compatible `response_format`/tool-call pattern; validate with Pydantic; one retry on validation failure with the error message appended).

Prompt (`core/agent/prompts/profiler.md`, `version: 1`) must instruct the model to:
- identify the **legal entity** that issued the report and whether it is a **group/parent** or a **subsidiary** (and of whom);
- set `issuer` to the canonical brand/group name the entity belongs to (so BMW Group and BMW Finance N.V. share `issuer="BMW"` but differ in `legal_entity`, `entity_type`, `is_primary_issuer_report`);
- determine fiscal year, currency, and the unit in which the primary financial statements are presented;
- classify `doc_type`; news/unrelated content → `doc_type="news"`, `issuer=None`;
- give a one-line summary and a confidence.

Validation rules (code, generic): if `doc_type` is a financial report then `fiscal_year` and `reporting_currency` must be present, else lower confidence and log a warning. The catalog must expose both the issuer→entities mapping and per-entity year coverage.

---

## 10. Cleaning (`core/ingestion/cleaner.py`)

Pure functions, unit-tested:

1. Unicode normalisation (NFKC), ligature replacement (ﬁ → fi, etc.), collapse multiple spaces, keep newlines.
2. **Within-page line dedup**: remove exact-duplicate consecutive or near-consecutive lines when `dedupe_lines` is on (handles the Ford 2023 doubled text layer). Keep the first occurrence.
3. **Running header/footer removal**: compute, per document, lines that appear on ≥ `header_footer_min_page_ratio` of pages (after normalising digits to `#` so page numbers match); remove them from all pages.
4. Drop pages whose cleaned text is shorter than `min_chunk_chars` unless they contain tables.

---

## 11. Chunking (`core/ingestion/chunker.py`)

`ChunkingStrategy` Protocol with `FixedWindowChunker` implementation:

- Operates per page, never crosses pages.
- Window `chunk_chars` with `overlap_chars`, splitting preferentially on paragraph breaks, then sentence ends, then whitespace.
- **Provenance header** prepended to every chunk's text, built from the profile:
  `[{legal_entity} | {doc_type} FY{fiscal_year} | {reporting_currency} {reporting_unit} | p.{page}]`
  (fields that are None are omitted). The header is part of the embedded text by design.
- **Table chunks**: when `table_chunks` is on and pdfplumber returns tables for the page, render each table as a Markdown table (header row + rows, cells stripped), prepend the same provenance header plus the nearest preceding heading line if available, and emit as `chunk_type="table"`. Tables longer than `chunk_chars * 2` are split by rows with the header row repeated.
- Metadata flattened from the profile: `issuer, legal_entity, entity_type, is_primary_issuer_report, fiscal_year, doc_type, currency, sector`.

---

## 12. Indexing

### 12.1 Embeddings (`core/index/embeddings.py`)
`EmbeddingModel` Protocol: `embed_documents(texts) -> list[list[float]]`, `embed_query(text)`, `dimension`, `name`. Implementation: `SentenceTransformersEmbedding` (CPU, normalised). BGE models benefit from a query instruction prefix; make `query_prefix` configurable (default: `"Represent this sentence for searching relevant passages: "`).

### 12.2 Vector store (`core/index/vector_store.py`)
`VectorStore` Protocol: `upsert(chunks, vectors)`, `query(vector, k, filters) -> list[(chunk_id, score)]`, `get(chunk_ids)`, `count()`, `delete_document(document_id)`. Implementation: `ChromaVectorStore` with persistent client at `index_dir/chroma`. Filters are a dict mapping metadata field → value or list of values, translated to Chroma `where` with `$in`.

### 12.3 Lexical index (`core/index/lexical.py`)
`LexicalIndex` Protocol: `add(chunks)`, `query(text, k, filters)`, `save()`, `load()`. Implementation: `BM25Index` (rank-bm25). Tokeniser: lowercase; strip thousands separators inside numbers (`96,773` → `96773`) and also keep the original token; split on non-alphanumerics; keep tokens of length ≥ 2 or pure digits. Filters applied as a post-filter on candidate chunk metadata. Persist with pickle to `index_dir/bm25.pkl` together with chunk metadata.

### 12.4 Catalog (`core/index/catalog.py`)
JSON file + in-memory view. Methods: `add(profile)`, `documents()`, `issuers() -> dict[issuer, list[entity]]`, `coverage(issuer) -> dict[entity, list[fiscal_year]]`, `has(document_id)`, `find(issuer=None, legal_entity=None, fiscal_year=None, doc_type=None)`.

---

## 13. Hybrid retrieval (`core/index/hybrid.py`)

`HybridRetriever.search(query: str, filters: dict | None, k: int | None, issuers: list[str] | None) -> list[RetrievedChunk]`:

1. If `issuers` has > 1 entries and `per_issuer_round_robin` is on, run the retrieval once per issuer (filter `issuer=X`) and merge round-robin so each issuer contributes alternately; otherwise single pass.
2. Per pass: dense top `candidates_per_index` and lexical top `candidates_per_index`, both with filters.
3. **Reciprocal rank fusion**: `score = Σ 1 / (rrf_k + rank)` over the lists in which the chunk appears. Sort desc, take `top_k`.
4. **Page expansion**: if on, for chunks with `chunk_type="table"` or containing ≥ 8 numeric tokens, attach `expanded_text` = the full cleaned page text (capped at `page_expansion_max_chars`), deduplicating when several hits share a page.
5. Return `RetrievedChunk` with both ranks and the fused score (these go into the trace and `/why`).

Year is **never** a hard filter. It is a query term; BM25 matches it.

---

## 14. Facts layer

### 14.1 Metric registry (`core/facts/registry.yaml`)
Data, not code. Initial content:

```yaml
version: 1
metrics:
  revenue:
    label: Revenue
    aliases: [total revenues, revenues, group revenues, net sales, sales revenue, turnover, "revenue ($m)"]
    kind: flow
    search_hints: ["total revenues", "group revenues", "revenue"]
  net_income:
    label: Net income
    aliases: [net profit, profit for the year, net income attributable to common stockholders, net income attributable to the company, group net profit, result for the year, net result]
    kind: flow
    search_hints: ["net income", "net profit", "profit for the year", "result for the year"]
  profit_before_tax:
    label: Profit before tax
    aliases: [income before income taxes, earnings before tax, EBT, group profit before tax, profit before taxation]
    kind: flow
    search_hints: ["profit before tax", "income before income taxes"]
  operating_income:
    label: Operating income (EBIT)
    aliases: [income from operations, operating profit, EBIT, earnings before interest and taxes]
    kind: flow
    search_hints: ["income from operations", "operating profit", "EBIT"]
  interest_income:
    label: Interest income
    aliases: [interest income, interest and similar income]
    kind: flow
    search_hints: ["interest income"]

derived:
  net_margin:
    label: Net margin
    formula: "net_income / revenue"
    output: ratio
    requires_same_entity_year: true
  yoy_growth:
    label: Year-on-year growth
    formula: "(current - previous) / abs(previous)"
    inputs: {current: {metric: "$metric", year: "$year"}, previous: {metric: "$metric", year: "$year - 1"}}
    output: ratio
  cagr:
    label: CAGR
    formula: "(end / start) ** (1 / years) - 1"
    output: ratio
```

`core/facts/registry.py` loads and validates the registry, resolves aliases → canonical names (case-insensitive, punctuation-insensitive), and exposes derived metric definitions.

### 14.2 Extraction (`core/facts/extractor.py`)
For each financial document:
1. **Page selection**: for every metric in the registry, run hybrid search restricted to that `document_id` using each `search_hint`; union the pages; rank by number of hits; take top `pages_per_document`. Always include pages whose chunks are `chunk_type="table"` and contain a registry alias.
2. **LLM extraction** per selected page (`prompts/extractor.md`, `version: 1`), with JSON-schema output: list of `{metric_alias_seen, canonical_metric (must be in registry or "unknown"), fiscal_year, value, unit, currency, is_comparative, evidence_snippet}`. Instruct the model: extract **every fiscal year present** (comparative columns too); copy numbers exactly; parentheses mean negative; do not compute; do not guess units — use the page or profile unit; skip `unknown` metrics.
3. Map to `FactRecord` (compute `value_base`, set `source_fiscal_year` from the profile).

### 14.3 Reconciliation (`core/facts/reconcile.py`)
Group by `(legal_entity, metric, fiscal_year)`:
- Single source → confidence 0.7 (0.8 if from the original-year report, 0.6 if comparative only).
- Multiple sources agreeing within `agreement_tolerance_pct` → keep the original-year record as canonical, confidence 0.95, note "agrees with FY{other} report".
- Disagreement → keep all, mark the original-year one canonical with confidence 0.6, note `restated: FY{other} report shows {value}`.
- Records below `min_confidence_to_use` are stored but flagged `usable=False`.

### 14.4 Store (`core/facts/store.py`)
SQLite table `facts` mirroring `FactRecord` plus `canonical BOOL`, `usable BOOL`. Query API: `lookup(issuer=None, legal_entity=None, metric=None, years=None, canonical_only=True) -> list[FactRecord]`. Export to CSV.

### 14.5 Compute (`core/facts/compute.py`)
`compute(derived_name, **bindings)` evaluates registry formulas with `simpleeval` (no `eval`). Rules:
- Inputs must come from `FactRecord`s; the result carries the input fact IDs as provenance.
- If inputs span entities with **different currencies** and the formula is not a ratio of same-entity values → raise `CurrencyMismatch` with a human-readable caveat; ratio comparisons (margins, growth) are allowed.
- If any input is missing → return a structured "insufficient data" result listing what is missing and what the catalog *does* cover.

---

## 15. LLM client (`core/llm/client.py`)

`LLMProvider` Protocol: `chat(messages, tools=None, tool_choice=None, response_format=None, temperature=0.0, model=None) -> LLMResponse` where `LLMResponse` has `content`, `tool_calls`, `usage: Usage`, `model`, `raw`.

Implementation `OpenAICompatibleProvider` using the `openai` SDK with `base_url` and `api_key` from settings. Must:
- set OpenRouter headers `HTTP-Referer` and `X-Title` (configurable);
- request usage accounting (`extra_body={"usage": {"include": True}}`) and read `cost` when present; otherwise compute from the price table;
- apply timeout, retries with exponential backoff on 429/5xx/timeouts;
- expose `model` override per call (for `/model` switching and eval);
- never log the API key; never include the key in traces.

---

## 16. Agent (`core/agent/`)

### 16.1 Tools (`tools.py`) — Pydantic-typed, JSON-schema exported for the LLM

1. `list_catalog() -> {issuers: {issuer: [{legal_entity, entity_type, is_primary_issuer_report, fiscal_years, currency, sector, doc_type}]}, other_documents: [...]}`
2. `search_documents(query: str, issuers: list[str] | None, legal_entities: list[str] | None, fiscal_years: list[int] | None, doc_types: list[str] | None, k: int | None) -> {results: [{chunk_id, document_id, legal_entity, fiscal_year, page, text, rrf_score}]}`
   - `fiscal_years` here filters on the **document's** fiscal year and is advisory; the tool description tells the model that prior-year figures live inside later reports and that it should usually leave this unset.
3. `lookup_facts(issuer: str | None, legal_entity: str | None, metric: str, fiscal_years: list[int] | None) -> {facts: [FactRecord fields incl. confidence, page, note], coverage: {entity: [years]}, metric_resolved: str}`
   - Resolves aliases via the registry; returns coverage so the model can explain gaps.
4. `compute_metric(derived: str, inputs: dict) -> {value, formatted, inputs_used: [fact_id...], caveats: [...]}` or `{error, missing: [...], available: {...}}`
5. `final_answer(AgentAnswer)` — the only way to finish. Forces structured output.

Every tool result is truncated for the model (configurable char cap) but stored in full in the trace.

### 16.2 Loop (`agent.py`)
- Messages: system prompt + condensed history (last `max_history_turns`, assistant answers truncated to `history_answer_chars`, plus a `context slots` line from the session) + user message.
- Loop up to `max_steps`: call LLM with tools; execute tool calls (sequentially; tolerate multiple per step); append results; stop when `final_answer` is called.
- If `max_steps` reached without `final_answer`, make one last call with `tool_choice=final_answer`.
- If the model/provider does not support tool calling, fall back to a JSON "plan" protocol (single structured call returning the tool sequence, executed by code, then a second call to answer). Implement only if the primary models fail the tool-calling smoke test.
- Temperature from config; model override per request.

### 16.3 System prompt (`prompts/system.md`, `version: 1`) — required content
- Role: financial-report analyst assistant working **only** from the provided documents via tools.
- Procedure: resolve references in follow-ups using the conversation (state the resolved question); call `list_catalog` when scope is unclear; for numeric questions call `lookup_facts` first, then `search_documents` for corroboration/explanation; for narrative questions use `search_documents`; use `compute_metric` for every calculation; for comparisons across issuers retrieve for each issuer.
- Grounding rules: never state a figure that is not in a tool result; always state **legal entity, fiscal year, currency and unit**; if the entity found is not the group/parent (e.g. a financing subsidiary), say so explicitly and offer the nearest available group figure; if a figure is absent, say "not in the provided documents" and name what *is* available (from coverage); never convert currencies; compare ratios when currencies differ; mention restatements when the fact note says so; news documents are not evidence for issuer questions.
- Output: `final_answer` with concise Markdown, inline citations like `[BMW Finance N.V. FY2023 p.12]`, caveats listed, confidence set honestly.
- Model knowledge: if and only if the `show_model_knowledge` flag is on (injected into the prompt as a line), add a clearly labelled `model_knowledge_note` beginning "Outside the provided documents (unverified): …". Otherwise never use prior knowledge.

---

## 17. Sessions and memory (`core/agent/memory.py`)

`SessionStore` Protocol: `create()`, `get(session_id)`, `append(session_id, turn)`, `update_slots(session_id, slots)`. Implementation `SQLiteSessionStore` (table `sessions(session_id, created_at, json)`). In-memory implementation for tests. Slots updated after each answer from `AgentAnswer.entities_used/years_used/metrics_used`. The CLI `/reset` creates a new session.

---

## 18. Observability and tracing (`core/observability/tracing.py`)

- One `Trace` per request, written as a JSON line to `traces_dir/YYYY-MM-DD.jsonl`, and the latest trace also kept in memory for `/why`.
- Store full tool results under `traces_dir/results/{trace_id}/{step}.json`.
- Structured logging via `logging` with a JSON formatter; log level from env.
- `ChatService.explain(trace_id)` returns a human-readable rendering: resolved question, tool calls, retrieved chunks with ranks/scores/pages, facts used, usage and cost, latency.

---

## 19. Service layer and API

`core/service.py`: `ChatService` composes everything from `Settings`: `chat(session_id, message, model=None, show_model_knowledge=None) -> (AgentAnswer, Trace)`, `catalog()`, `facts(...)`, `explain(trace_id)`, `ingest(path, force)`. CLI, API, Streamlit and eval all use `ChatService`; none re-implements logic.

`api/main.py` (FastAPI):
- `POST /chat` `{session_id?: str, message: str, model?: str, show_model_knowledge?: bool}` → `{session_id, answer: AgentAnswer, usage, trace_id, latency_ms}`
- `POST /sessions` → `{session_id}`
- `GET /catalog`
- `GET /facts?issuer=&legal_entity=&metric=&years=`
- `GET /traces/{trace_id}`
- `POST /ingest` `{path?: str, force?: bool}` → summary (synchronous for now; document that it would be a background job at scale)
- `GET /health` → `{status, model, documents, chunks, facts}`

---

## 20. CLI (`apps/cli.py`, typer + rich)

`python -m apps.cli chat [--model M] [--api-url URL] [--show-model-knowledge] [--replay]`

In-process `ChatService` by default; `--api-url` switches to HTTP against the API with the same behaviour.

Commands inside the chat:
- plain text → ask
- `/why` → explain the last trace (tool calls, chunks with scores and pages, facts with confidence, cost, latency)
- `/catalog` → table of discovered documents (entity, type, FY, currency, pages, chunks, facts)
- `/facts <issuer> [metric] [years]` → table from the facts store
- `/model <id>` → switch model for subsequent turns (prints the change)
- `/reset` → new session
- `/replay on|off` → toggle replay cache
- `/help`, `/quit`

Rendering: answer as Markdown panel; citations list; caveats in a yellow panel; footer line `model · tokens in/out · cost · latency · trace_id`.

---

## 21. Streamlit UI (`apps/streamlit_app.py`)

Minimal: chat history, model selector (from config), toggles for `show_model_knowledge`, an expander per answer showing citations/caveats/trace summary. Uses `ChatService` in-process. ≤ 150 lines.

---

## 22. Evaluation harness and golden set

### 22.1 `eval/golden.json` schema
```json
{
  "id": "q01",
  "category": "lookup | comparison | trend | narrative | entity_trap | out_of_scope | noise | followup | units | restatement",
  "turns": ["What was Tesla's revenue in 2023?"],
  "expect": {
    "behaviour": "answer | refuse | caveat",
    "values": [{"legal_entity_contains": "Tesla", "metric": "revenue", "fiscal_year": 2023, "value_base": 96773000000, "tolerance_pct": 0.5}],
    "must_cite": [{"file_name_contains": "Tesla_Annual_Report_2023", "pages_any_of": []}],
    "must_not_cite_doc_types": ["news"],
    "must_mention_any": [],
    "must_mention_all": [],
    "must_not_mention": []
  }
}
```
Multi-turn cases list several `turns`; only the last turn is scored unless `score_all_turns` is true.

### 22.2 Scorers (`eval/scorers.py`)
- `behaviour_match`: from `AgentAnswer.answered` and presence of caveats.
- `numeric_match`: parse numbers from `answer_markdown` with unit words (million/billion/bn/m, thousands separators, parentheses negatives), normalise to base units, compare within tolerance to each expected value.
- `retrieval_hit`: any `must_cite` document (and page if listed) appears in the trace's retrieved chunks or facts used.
- `citation_present`: ≥ 1 citation in the answer.
- `noise_leak`: any citation to `must_not_cite_doc_types`.
- `mentions`: substring checks (case-insensitive).
- Aggregate per category and overall; also report mean latency, tokens and cost per question.

### 22.3 Runner (`eval/run_eval.py`)
`python -m eval.run_eval --model <id> [--cases q01,q05] [--replay]` → fresh session per case → `eval/reports/report_<model-slug>_<date>.md` with a summary table, per-case rows, and failures with the trace ID. `make eval` runs both configured models and writes `eval/reports/latest.md` comparing them.

### 22.4 Golden set contents (initial; every value **VERIFY** against the PDFs and fill pages before use)

| id | category | turns | expected |
|---|---|---|---|
| q01 | lookup | Tesla revenue 2023 | answer; Tesla, Inc. FY2023 revenue 96,773 $M |
| q02 | lookup | Tesla revenue 2022 | answer; 81,462 $M |
| q03 | lookup | Ford revenue 2023 | answer; 176,191 $M (Ford Motor Company) |
| q04 | lookup | Ford revenue 2020 | answer (from FY2021 report comparatives); **VERIFY value** (~127,144 $M) |
| q05 | lookup | BMW revenue 2017 | answer (from BMW Group 2021 five-year table); 98,282 €M |
| q06 | entity_trap | BMW total revenue 2023 | caveat; must mention "BMW Finance N.V."; must mention latest Group revenue available (FY2021: 111,239 €M); must not present a BMW Group 2023 revenue figure |
| q07 | entity_trap | BMW profit figures 2020 and 2023 | caveat; 2020 Group net profit 3,857 €M; 2023 = BMW Finance N.V. result (**VERIFY value and unit**, appears as (112,654) in € thousands → −112.7 €M) |
| q08 | comparison | Tesla vs Ford higher profits 2022 | answer; Tesla net income 12,587 $M (or 12,556 attributable) vs Ford −1,981 $M; must mention margin or absolute comparison; same currency so no currency caveat required |
| q09 | lookup | Tesla profit 2022 and 2023 | answer; 12,587 / 14,974 $M (or attributable 12,556 / 14,997) |
| q10 | comparison | Better profitability 2022 overall | caveat; computed net margins Tesla ≈15.4%, Ford ≈ −1.3%; BMW: Group 2022 not available, BMW Finance N.V. not comparable; currency caveat |
| q11 | trend | Revenue summary Tesla, BMW, Ford past three years | caveat; Tesla 2021–2023, Ford 2021–2023, BMW Group 2019–2021 (latest available) + note on BMW Finance 2022/2023 |
| q12 | trend | BMW growth trends 2020–2023 | caveat; Group revenue 98,990 → 111,239 (+12.4%), net profit 3,857 → 12,463; 2022–2023 Group figures not in documents; BMW Finance N.V. results noted separately |
| q13 | narrative | Economic factors Ford 2021 | answer; must_mention_any: semiconductor, chip, commodity, COVID, pandemic, supply chain; must_not_cite news |
| q14 | narrative | Tesla product in development | answer; must_mention_any: Roadster; should state as-of FY2023 report |
| q15 | out_of_scope | Toyota revenue 2023 | refuse; must mention catalog coverage |
| q16 | noise | "What did the central bank do about interest rates?" | answer from news doc, or refuse as off-topic — **DECISION: answer, cite news.pdf, and note it is not an annual report** |
| q17 | followup | ["Tesla revenue 2023?", "And 2022?", "And Ford?"] | last turn: Ford FY2022 revenue 158,057 $M |
| q18 | followup | ["Ford revenue 2023?", "And their net income?"] | Ford FY2023 net income attributable 4,347 $M |
| q19 | units | "Tesla 2023 revenue in billions?" | answer; 96.8 $bn within tolerance |
| q20 | restatement | "Tesla 2022 revenue per the 2022 report vs the 2023 report" | answer; both 81,462 → note "agrees" |
| q21 | entity_trap | "BMW Group revenue 2023" (explicit) | refuse/caveat; must mention Finance N.V. and FY2021 as latest Group |
| q22 | comparison | "Tesla vs BMW revenue 2021" | caveat; 53,823 $M vs 111,239 €M; currency caveat; no conversion |

---

## 23. Replay cache (`scripts/record_replay.py`, `ChatService` flag)

- Key: sha256 of `(model, prompt versions, ordered session messages)`.
- `record` mode writes the `AgentAnswer` + `Trace` for every call to `replay_cache`.
- `--replay` mode serves from cache when the key matches, marks `trace.replayed=True`, and prints `[replay]` in the CLI; misses fall through to the live model.
- `make record-demo` runs the demo script (section 28) in record mode.

---

## 24. Tests (`tests/`, pytest)

Unit (no network): cleaner (dedup, header/footer), chunker (boundaries, overlap, header, table chunks), tokeniser, RRF, registry alias resolution, compute (formulas, currency mismatch, missing inputs), reconcile (agree/disagree/comparative), numeric parser in scorers, session slots, config loading.
Integration (marked, optional network): profiler on two fixture pages (BMW Finance N.V. page → `entity_type=subsidiary`), tool-calling smoke test, one end-to-end `chat` call with a mocked LLM provider that returns scripted tool calls.
Fixtures: small synthetic PDFs generated in tests (no dependency on the real dataset).

---

## 25. Docker

- `Dockerfile`: `python:3.11-slim`, install CPU torch from the PyTorch CPU index, copy code, pre-download the embedding model at build time, expose 8000 and 8501, default command runs the API; `streamlit` as an alternate command.
- `docker-compose.yml`: `api` and `ui` services sharing a `data/` volume; `.env` passed through; healthcheck on `/health`.
- `make docker-build`, `make docker-up`. Document that ingestion runs inside the container via `docker compose run api python scripts/ingest.py`.

---

## 26. Documentation deliverables

- **README.md** (≤ 2 screens): one-paragraph pitch; Mermaid architecture diagram; quickstart (`make setup`, `make ingest`, `make chat`); a short real transcript; decision table; eval results table (both models); link to `docs/DATA_AUDIT.md`; limitations; roadmap; how to add documents.
- **docs/DATA_AUDIT.md**: the table from section 2 with what the system discovered automatically (paste the `/catalog` output), the anomalies, and how each is handled generically.
- **docs/DECISIONS.md**: one row per decision — choice, why, alternatives considered, when to revisit.
- **docs/ARCHITECTURE.md**: ingestion and query sequence diagrams (Mermaid); the scaling section (10× / 100× / 1000× documents; multi-user; new domains) naming the interface to swap at each step.
- **docs/DEMO.md**: section 28.

---

## 27. Milestones with acceptance criteria

**M0 — Scaffold**
Repo, `pyproject.toml`, `requirements*.txt`, `Makefile` (`setup`, `lint`, `test`, `ingest`, `chat`, `api`, `ui`, `eval`, `record-demo`, `docker-build`), `config.yaml`, `.env.example`, `.gitignore` (`.env`, `data/index/`, `__pycache__`, `.venv`), `core/config.py`, `core/models.py`, empty packages, CI-less. `make lint test` passes (tests may be minimal).
*Accept:* `python -c "from core.config import settings; print(settings.chunking.chunk_chars)"` prints 800.

**M1 — Ingestion, profiling, catalog**
Loaders, cleaner, chunker, profiler, catalog, `scripts/ingest.py`.
*Accept:* `make ingest` processes all 9 documents; `/catalog` (or `python -m apps.cli catalog`) shows BMW → {BMW Group (group, FY2021), BMW Finance N.V. (subsidiary, FY2022, FY2023)}, Ford (FY2021–2023), Tesla (FY2022–2023), news as `doc_type=news`; Ford 2023 chunk count is within 30% of Ford 2022's; re-running `make ingest` skips all documents.

**M2 — Hybrid retrieval**
Embeddings, Chroma, BM25, RRF, page expansion, filters.
*Accept:* a `scripts/search.py "BMW group revenues 2017" --issuer BMW` returns the five-year-table page in the top 3; "Tesla total revenues 2023" returns the income statement/MD&A page; a Ford query never returns a news chunk when `issuer=Ford`.

**M3 — Facts layer**
Registry, extractor, reconcile, store, compute, `facts_export.csv`.
*Accept:* `/facts Tesla revenue` lists 2021–2023 with pages and confidence; BMW Group revenue 2017–2021 present; Ford 2020 present as comparative; at least one "agrees with" reconciliation note; `compute_metric net_margin` works and raises `CurrencyMismatch` for a Tesla-vs-BMW absolute difference. Owner verifies values against PDFs and records results in `eval/verification.md`; extraction accuracy ≥ 90% on verified facts (fix prompts if not).

**M4 — Agent, tools, memory, CLI**
*Accept:* all 12 brief questions produce grounded answers with citations in the CLI; follow-up chains q17 and q18 work; q06 and q21 produce the entity caveat; q15 refuses with coverage; `/why` shows tool calls and chunk scores; `/model` switches models live.

**M5 — Tracing, eval harness, golden set, reports**
*Accept:* `make eval` produces reports for both models; overall numeric accuracy ≥ 85% and zero noise leaks on the primary model; `eval/reports/latest.md` committed.

**M6 — API, Streamlit, Docker**
*Accept:* `make api` + `curl /health` works; `/chat` returns the same answer object as the CLI; `docker compose up` serves `/health`; Streamlit answers a question.

**M7 — Documentation, demo, replay**
*Accept:* README, DATA_AUDIT, DECISIONS, ARCHITECTURE, DEMO complete; `make record-demo` fills the replay cache; `python -m apps.cli chat --replay` reproduces the demo with `[replay]` markers; `git log` shows ≥ 10 meaningful commits; `grep -r OPENROUTER_API_KEY` finds only `.env.example` and config code.

---

## 28. Demo script (also `docs/DEMO.md`)

Order is deliberate: safe start → judgment moments → robustness.

1. `/catalog` — show what was *discovered*, including BMW Finance N.V. as a subsidiary.
2. "What was Tesla's revenue in 2023?" — citation + cost footer.
3. "And 2022?" — follow-up (year swap).
4. "And Ford?" — follow-up (issuer swap) → `/why`.
5. "What was BMW's total revenue in 2023?" — entity caveat, latest Group figure offered.
6. "Can you provide the revenue figures for BMW in 2017?" — answered from the five-year table.
7. "Between Tesla and Ford, which company achieved higher profits in 2022?" — facts + computed margin.
8. "Which company recorded better profitability in 2022 overall?" — currency caveat, ratios.
9. "What were the growth trends for BMW's financial performance from 2020 to 2023?" — computed growth, honest gap.
10. "What key economic factors influenced Ford's performance in 2021?" — narrative, no news leakage.
11. "Which Tesla product is currently in the development stage?" — narrative.
12. `/model google/gemini-3.1-pro-preview` → repeat 5 — model comparison (only if rehearsal shows a difference worth discussing).
13. "What was Toyota's revenue in 2023?" — out-of-scope with coverage.
14. (Optional) `--show-model-knowledge` once, repeat 5, to discuss grounded vs. remembered.

Fallback: `--replay` if the API misbehaves; say so.

---

## 29. Definition of done

- [ ] All milestone acceptance checks pass and are recorded in `docs/DEMO.md` or the README.
- [ ] `make lint test` clean.
- [ ] No dataset-specific strings in `core/` (grep for "BMW", "Ford", "Tesla", "Finance N.V." returns hits only in docs, eval, tests fixtures, and data).
- [ ] Every tunable in `config.yaml`; every prompt file has a `version:` header; traces record prompt versions.
- [ ] Eval reports for both models committed; the README table matches them.
- [ ] Private GitHub repo; `.env` never committed (check history).
- [ ] Owner has read every file and can explain each module's purpose and the reason for each decision in `docs/DECISIONS.md`.
- [ ] Demo rehearsed twice end-to-end, once with `--replay`.

---

## 30. Explicitly out of scope (list as roadmap in README)

Reranker model; Docling/Unstructured table-aware parsing; section-aware chunking; Qdrant/pgvector; Redis sessions; background ingestion jobs; authentication; multi-tenant isolation; currency conversion with FX data; fine-tuning; cloud deployment; prompt-injection hardening of document content; GraphRAG.
