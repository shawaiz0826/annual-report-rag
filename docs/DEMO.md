# Demo script

Order is deliberate: safe start → judgment moments → robustness. Rehearse twice, once with `--replay`.

```
make chat                      # live
make chat ARGS=--replay        # or: python -m apps.cli chat --replay  (recorded answers, marked [replay])
```

| # | Type | Say / ask | What to point at |
|---|---|---|---|
| 1 | `/catalog` | — | What was *discovered*: BMW Finance N.V. profiled as a subsidiary reporting in EUR thousands; BMW Group coverage ends FY2021; news isolated. Nothing here was configured. |
| 2 | ask | What was Tesla's revenue in 2023? | Citation `[Tesla, Inc. FY2023 p.20]`, unit and currency stated, footer with tokens/cost/latency/trace id. |
| 3 | ask | And 2022? | Follow-up resolved from slot memory (year swap); figure agrees across the 2022 and 2023 reports (confidence 0.95). |
| 4 | ask | And Ford? | Issuer swap. Then `/why`: tool calls, fact ids, pages, the two retrieval ranks and RRF score, cost. |
| 5 | ask | What was BMW's total revenue in 2023? | The entity trap: the answer says the 2023 document is BMW Finance N.V. (financing subsidiary), gives the latest group figure (FY2021, €111,239 million) and lists caveats. |
| 6 | ask | Can you provide the revenue figures for BMW in 2017? | Answered from the five-year table inside the FY2021 report; `is_comparative` noted. |
| 7 | ask | Between Tesla and Ford, which company achieved higher profits in 2022? | Facts for both; `compute_metric` difference/margins — arithmetic in Python with fact-id provenance. |
| 8 | ask | Which company recorded better profitability in 2022 overall? | Currency caveat (EUR vs USD): ratios compared, absolute amounts not; BMW Group 2022 not available, BMW Finance not comparable. |
| 9 | ask | What were the growth trends for BMW's financial performance from 2020 to 2023? | Computed growth 2020→2021, honest gap for 2022–2023 at group level, subsidiary results separately. |
| 10 | ask | What key economic factors influenced Ford's performance in 2021? | Narrative retrieval, issuer-filtered; no news leakage (check sources). |
| 11 | ask | Which Tesla product is currently in the development stage? | Narrative; "as of the FY2023 report". |
| 12 | `/model google/gemma-4-31b-it:free` then repeat 5 | — | Model switch live; compare caveat quality. (Skip if rehearsal shows no difference worth discussing, or if the free endpoint is rate-limited.) |
| 13 | ask | What was Toyota's revenue in 2023? | Out of scope: refuses and lists what the catalog does cover. |
| 14 | optional | restart with `--show-model-knowledge`, repeat 5 | Grounded answer vs a clearly labelled "Outside the provided documents (unverified)" note — the grounding story. |

Fallbacks, said out loud if used:
- `--replay`: serves recorded answers (`[replay]` in the footer) when the API misbehaves — free endpoints can return 429 upstream.
- `/model anthropic/claude-sonnet-5`: paid model, same pipeline, if credits are loaded.

## Acceptance checks recorded

| Milestone | Check | Result |
|---|---|---|
| M0 | `python -c "from core.config import settings; print(settings.chunking.chunk_chars)"` | `800`; `make lint test` green |
| M1 | `make ingest` processes 9 documents; catalog shows BMW Group (group, FY2021), BMW Finance N.V. (subsidiary, FY2022/23), Ford FY2021–23, Tesla FY2022–23, news; Ford 2023 chunks within 30 % of Ford 2022; re-run skips all | ✅ 9 ingested; 571 vs 541 chunks (+6 %); re-run `{'skipped': 9}` |
| M2 | `scripts/search.py "BMW group revenues 2017" --issuer BMW` → five-year table page top 3; "Tesla total revenues 2023" → income statement page; Ford query never returns news | ✅ p.10 rank 1; p.20 FY2023 rank 2; 0 news chunks in 5 issuer-filtered queries |
| M3 | facts for Tesla revenue 2021–2023, BMW Group revenue 2017–2021, Ford 2020 comparative, ≥ 1 "agrees with" note, `compute net_margin`, `CurrencyMismatch`; extraction accuracy ≥ 90 % | see `eval/verification.md` |
| M4 | 12 brief questions grounded with citations; follow-ups; entity caveats; `/why`; `/model` | see README transcript |
| M5 | `make eval` reports for both models; numeric accuracy ≥ 85 %, zero noise leaks on the primary | see `eval/reports/latest.md` |
| M6 | `make api` + `curl /health`; `/chat` returns the same answer object as the CLI; `docker compose up`; Streamlit answers | see README |
| M7 | docs complete; `make record-demo`; `--replay` reproduces; ≥ 10 commits; secrets grep | see README |
