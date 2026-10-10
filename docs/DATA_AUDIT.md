# Data audit

What the nine provided files actually are, what the system discovered about them without being told, and how each anomaly is handled **generically** (no company, year, currency or file name appears in `core/`).

## What the system discovered at ingest (`make catalog`)

| issuer | legal entity | doc type | entity type | primary report | FY | currency | unit | pages | chunks | file |
|---|---|---|---|---|---|---|---|---|---|---|
| BMW | BMW Group | annual_report | group | yes | 2021 | EUR | millions | 50 | 269 | BMW_Annual_Report_2021.pdf |
| BMW | BMW Finance N.V. | annual_report | subsidiary | no | 2022 | EUR | thousands | 54 | 257 | BMW_Annual_Report_2022.pdf |
| BMW | BMW Finance N.V. | annual_report | subsidiary | no | 2023 | EUR | thousands | 55 | 335 | BMW_Annual_Report_2023.pdf |
| Ford | Ford Motor Company | annual_report | group | yes | 2021 | USD | millions | 75 | 514 | Ford_Annual_Report_2021.pdf |
| Ford | Ford Motor Company | annual_report | group | yes | 2022 | USD | millions | 76 | 541 | Ford_Annual_Report_2022.pdf |
| Ford | Ford Motor Company | annual_report | group | yes | 2023 | USD | millions | 75 | 571 | Ford_Annual_Report_2023.pdf |
| Tesla | Tesla, Inc. | annual_report | group | yes | 2022 | USD | millions | 62 | 488 | Tesla_Annual_Report_2022.pdf |
| Tesla | Tesla, Inc. | annual_report | group | yes | 2023 | USD | millions | 66 | 443 | Tesla_Annual_Report_2023.pdf |
| – | – | news | – | no | – | – | – | 9 | 32 | news.pdf |

Every value in that table comes from the profiler (an LLM reading the first pages, one middle page, the first statements page and the unit declarations found across the document) plus generic validation rules. Nothing was configured by hand. `news.docx` is a duplicate of `news.pdf` and is not ingested twice (same content, different container; the DOCX loader is exercised by the test-suite instead).

## Anomalies and how they are handled

### 1. "BMW" is two different legal entities

`BMW_Annual_Report_2022.pdf` and `_2023.pdf` are the annual reports of **BMW Finance N.V.**, the Dutch financing subsidiary ("a wholly owned subsidiary of BMW Holding B.V. … BMW AG"). They present in **EUR thousand**, contain no vehicle revenue, and their result is interest-margin and derivative driven (FY2023: a net loss of €394.3 million). Only `BMW_Annual_Report_2021.pdf` is a **BMW Group** report.

*Handling:* the profiler separates `issuer` ("BMW" — the brand both documents belong to) from `legal_entity`, `entity_type` and `is_primary_issuer_report`. Facts are keyed by legal entity. The agent's system prompt is generated from the catalog, so it knows before the first tool call that BMW Group coverage ends in FY2021 and that 2022/2023 belong to the subsidiary; it is instructed to say so explicitly and to offer the nearest group figure. Eval cases q06, q07, q21 test exactly this.

### 2. The year asked for is rarely the year of the document

BMW Group 2021 contains a five-year table (FY2017–2021). Ford and Tesla 10-K extracts carry two or three comparative years. "Ford 2020" lives in the FY2021 report.

*Handling:* fiscal year is never a hard retrieval filter (it is a query term BM25 matches). The extractor emits one fact per fiscal-year column; each fact records `source_fiscal_year` and `is_comparative`. Reconciliation then cross-checks a figure that appears in several reports (confidence 0.95 "agrees with FY… report") and flags disagreements as restatements.

### 3. Ford's FY2023 PDF has a corrupt text layer

The file is ~582k characters where the other Ford years are ~270k. It is **not** "every line duplicated": the producer drew each page two or three times — once as the body, then again with a running header overlaid, with a vertical offset that drifts down the page. pdfplumber interleaves the layers into garbage like `IITTEEMM 11CC.. CCyybbeerrsseeccuurriittyy` and `asseTmhbel yfa ocfi liotiuers`, and layout analysis takes 10× longer.

*Handling (loader, generic):* characters arrive in content-stream order, so each layer is a contiguous *run* that restarts near the top of the page. `drop_duplicate_runs` keeps the first run and drops later runs whose tokens are ≥ 80 % covered by what was kept (multi-column layouts also produce several runs, but their tokens differ, so they survive — verified: zero runs dropped across the other eight files). Residual doubled glyphs are collapsed in the cleaner by a ratio-guarded greedy collapse (`"mmiillliioonn"` → `"million"`, `"balloon"` untouched). Result: 297k clean characters, 571 chunks vs 541 for FY2022, extraction in 23 s instead of 210 s.

### 4. Running headers and footers

Every file repeats page furniture (`"10 BMW Group Report 2021 To Our Stakeholders …"`, `"Item 7. Management's Discussion … (Continued)"`, page numbers).

*Handling:* lines in the first/last three lines of a page are digit-masked and counted across pages; a line on ≥ 50 % of pages, or a long line (≥ 25 chars) on ≥ 15 % of pages, is removed everywhere. Body lines such as `"Total"` cannot qualify because they are outside the zone.

### 5. Ford's extract has no income statement

The Ford files are 10-K *extracts*: the consolidated statements are not included. Company revenue and net income appear only in the MD&A "Company key metrics" tables (`Revenue ($M) 158,057 176,191 11 %`), with the **earlier year in the first column** and segment tables (Ford Blue, Model e, Pro) laid out identically on the following pages.

*Handling:* the registry alias `revenue ($m)` catches the label; the extractor is told to use column headers for year assignment and to skip segment figures; `eval/verified_facts.json` holds the hand-checked values and `python -m eval.verify_facts` scores extraction against them.

### 6. Currencies differ

BMW reports in EUR, Ford and Tesla in USD.

*Handling:* `compute_metric` refuses to combine facts in different currencies (`CurrencyMismatch`) and tells the model to compare ratios instead; the system prompt forbids conversion. Eval cases q10 and q22 test it.

### 7. Deliberate noise

`news.pdf` is nine pages of unrelated articles (elections, football, a film, a central-bank rate decision).

*Handling:* profiled as `doc_type=news`, `issuer=null`; issuer-filtered retrieval can never return it; the system prompt says news is not evidence for company questions; the eval checks every company answer for news citations (`noise_leak`). It remains answerable on its own (q16) with a note that it is not an annual report.

### 8. pdfplumber tables are sparse

Table extraction returns grids with many empty columns and occasionally splits a statement across cells.

*Handling:* empty columns are dropped when rendering Markdown table chunks; every table chunk and every numeric-dense chunk is **page-expanded** at retrieval time (the full cleaned page text is attached) so the model sees the statement in context; the extractor receives both the page text and the raw cell grids.

## Reproduction

```
make ingest           # profiles + indexes + extracts (idempotent)
make catalog          # the table above
make facts ARGS="BMW net income"
python -m eval.verify_facts
```
