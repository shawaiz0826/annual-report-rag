# Eval report — `nvidia/nemotron-3-ultra-550b-a55b:free` — 2026-10-12 (PARTIAL — continue with --resume)

System prompt version(s) answered under: 2. Model fallback disabled for evals; every answer is live.

| cases | passed | numeric accuracy | behaviour accuracy | noise leaks | mean latency | mean tokens | total cost |
|---|---|---|---|---|---|---|---|
| 16 | 13 (81%) | 83% (12 cases) | 100% | 0 | 33.1s | 19872 | $0.0000 |

## By category

| category | passed / total |
|---|---|
| comparison | 1 / 2 |
| entity_trap | 2 / 2 |
| lookup | 6 / 6 |
| narrative | 1 / 2 |
| noise | 1 / 1 |
| out_of_scope | 1 / 1 |
| trend | 1 / 2 |

## Cases

| id | category | pass | behaviour | numeric | retrieval | citation | leak | mentions | latency | tokens | trace |
|---|---|---|---|---|---|---|---|---|---|---|---|
| q01 | lookup | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 9s | 7294 | `5353352ac511` |
| q02 | lookup | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 54s | 7352 | `d8973e2ae4fc` |
| q03 | lookup | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 54s | 7323 | `e8bf93c60be9` |
| q04 | lookup | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 19s | 14449 | `ff49a66645a2` |
| q05 | lookup | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 99s | 17427 | `829eafc4885a` |
| q06 | entity_trap | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 17s | 8776 | `1de68308d83a` |
| q07 | entity_trap | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 22s | 9568 | `8c97cced4669` |
| q08 | comparison | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 28s | 16469 | `55977753189d` |
| q09 | lookup | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 11s | 7686 | `c115f66cea30` |
| q10 | comparison | ❌ | ✅ | ❌ | ✅ | ✅ | – | ✅ | 30s | 37057 | `042700ad84ef` |
| q11 | trend | ✅ | ✅ | ✅ | ✅ | ✅ | – | ✅ | 29s | 31197 | `84e3ddc79451` |
| q12 | trend | ❌ | ✅ | ❌ | ✅ | ✅ | – | ✅ | 51s | 52175 | `7ccb8e9001d3` |
| q13 | narrative | ❌ | ✅ | – | ✅ | ✅ | – | ❌ | 27s | 62116 | `c013f670e0ca` |
| q14 | narrative | ✅ | ✅ | – | ✅ | ✅ | – | ✅ | 18s | 14799 | `569939b9340a` |
| q15 | out_of_scope | ✅ | ✅ | – | ✅ | ✅ | – | ✅ | 14s | 8614 | `c8f120f87adc` |
| q16 | noise | ✅ | ✅ | – | ✅ | ✅ | – | ✅ | 50s | 15650 | `a6e8a7e694ca` |

## Failures (3)

### q10 — Which company recorded better profitability in 2022 overall?

- why: values Tesla net_margin FY2022=0.1545; Ford net_margin FY2022=-0.0125
- trace: `042700ad84ef`
- answer:

  > Based on the provided documents, **Tesla, Inc.** recorded better profitability in 2022 than Ford Motor Company. BMW Group's 2022 profitability is not available in the provided documents (only its subsidiary BMW Finance N.V. has 2022 data).
- caveats: ["BMW Group's 2022 net income and revenue are not in the provided documents; only BMW Finance N.V. (a financing subsidiary) has 2022 figures (EUR 403,943 thousand net income). Subsidiary figures do not represent group profitability.", 'Ford and Tesla report in USD; BMW reports in EUR. Currency differences prevent direct absolute comparison, but net margin ratios are comparable.', 'Profitability assessed via net margin (net income / revenue) for Ford and Tesla. Other profitability metrics (operating margin, EBITDA margin) are not computed here.']

### q12 — What were the growth trends for BMW's financial performance from 2020 to 2023?

- why: values BMW revenue FY2020=98990000000; BMW revenue FY2021=111239000000; BMW net_income FY2021=12463000000
- trace: `7ccb8e9001d3`
- answer:

  > Based on the provided documents, I can only report BMW Group's financial performance trends from **2020 to 2021**. The documents do not contain BMW Group (consolidated) financial statements for fiscal years 2022 or 2023 — only the 2021 group annual report (which includes comparative figures back to 2017) and the 2022/2023 annual reports of **BMW Finance N.V.**, a financing subsidiary that reports in EUR thousands and is not representative of the group's overall performance.
- caveats: ['BMW Group consolidated data is only available through FY2021 in the provided documents.', 'BMW Finance N.V. (subsidiary) reports for FY2022 and FY2023 are in EUR thousands and cover only the financing subsidiary, not the group.', 'Revenue and net income figures for FY2020 are comparative figures from the FY2021 report (confidence 0.6).', 'Currency and unit mismatch: Group reports in EUR millions; subsidiary reports in EUR thousands.', 'No group-level revenue, net income, or other performance metrics for FY2022 or FY2023 are present in the provided documents.']

### q13 — What key economic factors influenced Ford's performance in 2021?

- why: mentions none of ['semiconductor', 'chip', 'commodity', 'COVID', 'pandemic', 'supply chain', 'inflation']
- trace: `c013f670e0ca`
- answer:

  > Based on Ford Motor Company's FY2021 annual report, the following key economic factors influenced its performance in 2021, as identified in the **'Key Trends and Economic Factors Affecting Ford and the Automotive Industry'** section (Item 7, MD&A) [Ford Motor Company FY2021 p.34]:
- caveats: ["The analysis is based solely on Ford's FY2021 annual report (group entity, USD millions). No other years or entities were requested.", "The report identifies these factors as affecting the automotive industry broadly and Ford specifically; the company's causal-factor EBIT bridge (Volume/Mix, Net Pricing, Cost, Exchange, Other) quantifies their net impact on 2021 Automotive EBIT but is not broken out by individual economic factor."]

