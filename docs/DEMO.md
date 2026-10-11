# Walkthrough

Not a script: every answer is generated live by retrieval → facts → model, and the system answers whatever is asked. The questions below are the ones that show the judgment moments; ask them in any order, in your own words.

```
make chat                      # terminal chat (/help inside)
make ui                        # same service in the browser, with a model selector
```

| # | Type | Ask | What to point at |
|---|---|---|---|
| 1 | `/catalog` | — | What was *discovered* at ingest: BMW Finance N.V. profiled as a subsidiary reporting in EUR thousands; BMW Group coverage ends FY2021; `news.pdf` isolated. Nothing here was configured by hand. |
| 2 | ask | What was Tesla's revenue in 2023? | Citation `[Tesla, Inc. FY2023 p.20]`, unit and currency stated, footer with model, tokens, cost, latency and trace id. |
| 3 | ask | And 2022? | Follow-up resolved from slot memory (year swap); the figure agrees across the 2022 and 2023 reports (confidence 0.95). |
| 4 | ask | And Ford? | Issuer swap. Then `/why`: tool calls, fact ids, pages, both retrieval ranks and the RRF score, cost. |
| 5 | ask | What was BMW's total revenue in 2023? | The entity trap: the answer says the 2023 document is BMW Finance N.V. (financing subsidiary), gives the latest group figure (FY2021, €111,239 million) and lists caveats. |
| 6 | ask | Can you provide the revenue figures for BMW in 2017? | Answered from the five-year table inside the FY2021 report; `is_comparative` noted. |
| 7 | ask | Between Tesla and Ford, which company achieved higher profits in 2022? | Facts for both; `compute_metric` difference/margins — arithmetic in Python with fact-id provenance. |
| 8 | ask | Which company recorded better profitability in 2022 overall? | Currency caveat (EUR vs USD): ratios compared, absolute amounts not; BMW Group 2022 not available, BMW Finance not comparable. |
| 9 | ask | What were the growth trends for BMW's financial performance from 2020 to 2023? | Computed growth 2020→2021, honest gap for 2022–2023 at group level, subsidiary results separately. |
| 10 | ask | What key economic factors influenced Ford's performance in 2021? | Narrative retrieval, issuer-filtered; no news leakage (check the sources). |
| 11 | ask | Which Tesla product is currently in the development stage? | Narrative; "as of the FY2023 report". |
| 12 | `/model google/gemma-4-31b-it:free`, repeat 5 | — | Model switch live; compare caveat quality. |
| 13 | ask | What was Toyota's revenue in 2023? | Out of scope: refuses and lists what the catalog does cover. |
| 14 | optional | restart with `--show-model-knowledge`, repeat 5 | Grounded answer vs a clearly labelled "Outside the provided documents (unverified)" note — the grounding story. |

What happens when the model misbehaves (say it out loud if it does):
- A failed call (daily quota, upstream 429/5xx, missing endpoint) is retried on `LLM_MODEL_FALLBACK` automatically; the footer shows the model that answered and a caveat says why. `/why` prints the fallback reason.
- If every model is exhausted, the answer says so in plain language with the reset time, and states that the documents were not consulted — it never guesses.
- `/model anthropic/claude-sonnet-5`: paid model, same pipeline, once credits are loaded.

## Acceptance checks recorded

| Milestone | Check | Result |
|---|---|---|
| M0 | `python -c "from core.config import settings; print(settings.chunking.chunk_chars)"` | `800`; `make lint test` green |
| M1 | `make ingest` processes 9 documents; catalog shows BMW Group (group, FY2021), BMW Finance N.V. (subsidiary, FY2022/23), Ford FY2021–23, Tesla FY2022–23, news; Ford 2023 chunks within 30 % of Ford 2022; re-run skips all | ✅ 9 ingested; 571 vs 541 chunks (+6 %); re-run `{'skipped': 9}` |
| M2 | `scripts/search.py "BMW group revenues 2017" --issuer BMW` → five-year table page top 3; "Tesla total revenues 2023" → income statement page; Ford query never returns news | ✅ p.10 rank 1; p.20 FY2023 rank 2; 0 news chunks in 5 issuer-filtered queries |
| M3 | facts for Tesla revenue 2021–2023, BMW Group revenue 2017–2021, Ford 2020 comparative, ≥ 1 "agrees with" note, `compute net_margin`, `CurrencyMismatch`; extraction accuracy ≥ 90 % | ✅ 46/46 verified figures — `eval/verification.md` |
| M4 | 12 brief questions grounded with citations; follow-ups; entity caveats; `/why`; `/model` | ✅ live session 2026-10-10 — README transcript |
| M5 | `make eval` reports for both models; numeric accuracy ≥ 85 %, zero noise leaks on the primary | pending the live run — `eval/reports/latest.md` |
| M6 | `make api` + `curl /health`; `/chat` returns the same answer object as the CLI; `docker compose up`; Streamlit answers | ✅ `/health`, `/sessions`, `/chat` covered by `tests/test_api.py`; Dockerfile + compose provided (image not built in this environment) |
| M7 | docs complete; fresh clone → `make setup rebuild chat` answers; ≥ 10 commits; secrets grep finds only `.env.example` | ✅ fresh-clone check 2026-10-11; 20+ commits |
