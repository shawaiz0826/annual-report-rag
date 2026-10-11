# Fact extraction verification — 2026-10-11

Ground truth: 46 figures read by hand from the extracted page text (`eval/verified_facts.json`).
Stored canonical facts: 85. **Accuracy on verified figures: 46/46 = 100.0%** (tolerance 0.5%; attributable/consolidated alternatives accepted).

| status | legal entity | metric | FY | expected | stored | unit | page | conf | note |
|---|---|---|---|---|---|---|---|---|---|
| ✅ | Tesla, Inc. | revenue | 2023 | 96,773 millions | 96,773 | millions | 20 | 0.80 | also on p.26; also on p.63 |
| ✅ | Tesla, Inc. | revenue | 2022 | 81,462 millions | 81,462 | millions | 20 | 0.95 | also on p.59; agrees with FY2023 report |
| ✅ | Tesla, Inc. | revenue | 2021 | 53,823 millions | 53,823 | millions | 20 | 0.95 | also on p.26; also on p.63; agrees with FY2022 report |
| ✅ | Tesla, Inc. | revenue | 2020 | 31,536 millions | 31,536 | millions | 20 | 0.60 | also on p.59 |
| ✅ | Tesla, Inc. | net_income | 2023 | 14,974 millions | 14,974 | millions | 20 | 0.80 | also on p.32 |
| ✅ | Tesla, Inc. | net_income | 2022 | 12,587 millions | 12,587 | millions | 20 | 0.95 | also on p.31; agrees with FY2023 report |
| ✅ | Tesla, Inc. | net_income | 2021 | 5,644 millions | 5,644 | millions | 20 | 0.95 | agrees with FY2022 report |
| ✅ | Tesla, Inc. | operating_income | 2023 | 8,891 millions | 8,891 | millions | 20 | 0.80 |  |
| ✅ | Tesla, Inc. | operating_income | 2022 | 13,656 millions | 13,656 | millions | 20 | 0.95 | agrees with FY2023 report |
| ✅ | Tesla, Inc. | operating_income | 2021 | 6,523 millions | 6,523 | millions | 20 | 0.95 | agrees with FY2022 report |
| ✅ | Tesla, Inc. | profit_before_tax | 2023 | 9,973 millions | 9,973 | millions | 20 | 0.80 | also on p.54 |
| ✅ | Tesla, Inc. | profit_before_tax | 2022 | 13,719 millions | 13,719 | millions | 20 | 0.95 | agrees with FY2023 report |
| ✅ | Tesla, Inc. | profit_before_tax | 2021 | 6,343 millions | 6,343 | millions | 20 | 0.95 | also on p.54; agrees with FY2022 report |
| ✅ | Ford Motor Company | revenue | 2023 | 176,191 millions | 176,191 | millions | 44 | 0.80 |  |
| ✅ | Ford Motor Company | revenue | 2022 | 158,057 millions | 158,057 | millions | 39 | 0.95 | agrees with FY2023 report |
| ✅ | Ford Motor Company | revenue | 2021 | 136,341 millions | 136,341 | millions | 39 | 0.95 | agrees with FY2022 report; agrees with FY2023 report |
| ✅ | Ford Motor Company | revenue | 2020 | 127,144 millions | 127,144 | millions | 52 | 0.95 | agrees with FY2021 report |
| ✅ | Ford Motor Company | revenue | 2019 | 155,900 millions | 155,900 | millions | 52 | 0.60 |  |
| ✅ | Ford Motor Company | net_income | 2023 | 4,347 millions | 4,347 | millions | 44 | 0.80 | also on p.70 |
| ✅ | Ford Motor Company | net_income | 2022 | -1,981 millions | -1,981 | millions | 39 | 0.95 | also on p.71; agrees with FY2023 report |
| ✅ | Ford Motor Company | net_income | 2021 | 17,937 millions | 17,937 | millions | 39 | 0.95 | also on p.71 (rounded); agrees with FY2022 report; agrees with FY2023 report |
| ✅ | Ford Motor Company | net_income | 2020 | -1,279 millions | -1,279 | millions | 52 | 0.95 | also on p.71 (rounded); agrees with FY2021 report |
| ✅ | BMW Group | revenue | 2021 | 111,239 millions | 111,239 | millions | 10 | 0.80 |  |
| ✅ | BMW Group | revenue | 2020 | 98,990 millions | 98,990 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | revenue | 2019 | 104,210 millions | 104,210 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | revenue | 2018 | 96,855 millions | 96,855 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | revenue | 2017 | 98,282 millions | 98,282 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | net_income | 2021 | 12,463 millions | 12,463 | millions | 10 | 0.80 |  |
| ✅ | BMW Group | net_income | 2020 | 3,857 millions | 3,857 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | net_income | 2019 | 5,022 millions | 5,022 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | net_income | 2018 | 7,064 millions | 7,064 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | net_income | 2017 | 8,675 millions | 8,675 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | operating_income | 2021 | 13,400 millions | 13,400 | millions | 10 | 0.80 |  |
| ✅ | BMW Group | operating_income | 2020 | 4,830 millions | 4,830 | millions | 10 | 0.60 |  |
| ✅ | BMW Group | profit_before_tax | 2021 | 16,060 millions | 16,060 | millions | 10 | 0.80 |  |
| ✅ | BMW Group | profit_before_tax | 2020 | 5,222 millions | 5,222 | millions | 10 | 0.60 |  |
| ✅ | BMW Finance N.V. | net_income | 2023 | -394,288 thousands | -394,288 | thousands | 10 | 0.80 |  |
| ✅ | BMW Finance N.V. | net_income | 2022 | 403,943 thousands | 403,943 | thousands | 10 | 0.95 | also on p.13; agrees with FY2023 report |
| ✅ | BMW Finance N.V. | net_income | 2021 | 118,889 thousands | 118,889 | thousands | 10 | 0.60 | also on p.13; also on p.26 |
| ✅ | BMW Finance N.V. | profit_before_tax | 2023 | -506,962 thousands | -506,962 | thousands | 10 | 0.80 | also on p.22 |
| ✅ | BMW Finance N.V. | profit_before_tax | 2022 | 550,600 thousands | 550,600 | thousands | 10 | 0.95 | agrees with FY2023 report |
| ✅ | BMW Finance N.V. | profit_before_tax | 2021 | 155,682 thousands | 155,682 | thousands | 10 | 0.60 |  |
| ✅ | BMW Finance N.V. | interest_income | 2023 | 1,965,292 thousands | 1,965,292 | thousands | 10 | 0.80 | also on p.19 |
| ✅ | BMW Finance N.V. | interest_income | 2022 | 988,331 thousands | 988,331 | thousands | 10 | 0.95 | also on p.20; agrees with FY2023 report |
| ✅ | BMW Finance N.V. | total_assets | 2023 | 42,279,770 thousands | 42,279,770 | thousands | 11 | 0.80 |  |
| ✅ | BMW Finance N.V. | total_assets | 2022 | 43,220,688 thousands | 43,220,688 | thousands | 11 | 0.60 |  |

## Stored canonical facts without ground truth (39) — spot-check candidates

| legal entity | metric | FY | value | unit | page | file | conf |
|---|---|---|---|---|---|---|---|
| BMW Finance N.V. | interest_income | 2021 | 750,694 | thousands | 10 | BMW_Annual_Report_2022.pdf | 0.60 |
| BMW Finance N.V. | total_equity | 2021 | 306,475 | thousands | 13 | BMW_Annual_Report_2022.pdf | 0.60 |
| BMW Finance N.V. | total_equity | 2022 | 708,316 | thousands | 13 | BMW_Annual_Report_2022.pdf | 0.95 |
| BMW Finance N.V. | total_equity | 2023 | 317,032 | thousands | 11 | BMW_Annual_Report_2023.pdf | 0.80 |
| BMW Group | operating_income | 2017 | 9,899 | millions | 10 | BMW_Annual_Report_2021.pdf | 0.60 |
| BMW Group | operating_income | 2018 | 8,933 | millions | 10 | BMW_Annual_Report_2021.pdf | 0.60 |
| BMW Group | operating_income | 2019 | 7,411 | millions | 10 | BMW_Annual_Report_2021.pdf | 0.60 |
| BMW Group | profit_before_tax | 2017 | 10,675 | millions | 10 | BMW_Annual_Report_2021.pdf | 0.60 |
| BMW Group | profit_before_tax | 2018 | 9,627 | millions | 10 | BMW_Annual_Report_2021.pdf | 0.60 |
| BMW Group | profit_before_tax | 2019 | 7,118 | millions | 10 | BMW_Annual_Report_2021.pdf | 0.60 |
| Ford Motor Company | free_cash_flow | 2019 | 3 | billions | 52 | Ford_Annual_Report_2021.pdf | 0.60 |
| Ford Motor Company | free_cash_flow | 2020 | 1 | billions | 52 | Ford_Annual_Report_2021.pdf | 0.60 |
| Ford Motor Company | free_cash_flow | 2021 | 5 | billions | 54 | Ford_Annual_Report_2023.pdf | 0.95 |
| Ford Motor Company | free_cash_flow | 2022 | 9 | billions | 39 | Ford_Annual_Report_2022.pdf | 0.95 |
| Ford Motor Company | free_cash_flow | 2023 | 7 | billions | 44 | Ford_Annual_Report_2023.pdf | 0.80 |
| Ford Motor Company | net_income | 2019 | 47 | millions | 52 | Ford_Annual_Report_2021.pdf | 0.60 |
| Ford Motor Company | operating_income | 2021 | 10,000 | millions | 54 | Ford_Annual_Report_2023.pdf | 0.60 |
| Ford Motor Company | operating_income | 2022 | 10,415 | millions | 44 | Ford_Annual_Report_2023.pdf | 0.60 |
| Ford Motor Company | operating_income | 2023 | 10,416 | millions | 44 | Ford_Annual_Report_2023.pdf | 0.80 |
| Ford Motor Company | total_equity | 2019 | 33 | billions | 71 | Ford_Annual_Report_2021.pdf | 0.60 |
| Ford Motor Company | total_equity | 2020 | 31 | billions | 71 | Ford_Annual_Report_2022.pdf | 0.95 |
| Ford Motor Company | total_equity | 2021 | 49 | billions | 71 | Ford_Annual_Report_2021.pdf | 0.95 |
| Ford Motor Company | total_equity | 2022 | 43 | billions | 71 | Ford_Annual_Report_2022.pdf | 0.95 |
| Ford Motor Company | total_equity | 2023 | 43 | billions | 70 | Ford_Annual_Report_2023.pdf | 0.80 |
| Tesla, Inc. | gross_profit | 2020 | 6,630 | millions | 20 | Tesla_Annual_Report_2022.pdf | 0.60 |
| Tesla, Inc. | gross_profit | 2021 | 13,606 | millions | 20 | Tesla_Annual_Report_2023.pdf | 0.95 |
| Tesla, Inc. | gross_profit | 2022 | 20,853 | millions | 20 | Tesla_Annual_Report_2022.pdf | 0.95 |
| Tesla, Inc. | gross_profit | 2023 | 17,660 | millions | 20 | Tesla_Annual_Report_2023.pdf | 0.80 |
| Tesla, Inc. | interest_income | 2020 | 30 | millions | 20 | Tesla_Annual_Report_2022.pdf | 0.60 |
| Tesla, Inc. | interest_income | 2021 | 56 | millions | 20 | Tesla_Annual_Report_2023.pdf | 0.95 |
| Tesla, Inc. | interest_income | 2022 | 297 | millions | 20 | Tesla_Annual_Report_2022.pdf | 0.95 |
| Tesla, Inc. | interest_income | 2023 | 1,066 | millions | 20 | Tesla_Annual_Report_2023.pdf | 0.80 |
| Tesla, Inc. | net_income | 2020 | 862 | millions | 20 | Tesla_Annual_Report_2022.pdf | 0.60 |
| Tesla, Inc. | operating_income | 2020 | 1,994 | millions | 20 | Tesla_Annual_Report_2022.pdf | 0.60 |
| Tesla, Inc. | profit_before_tax | 2020 | 1,154 | millions | 20 | Tesla_Annual_Report_2022.pdf | 0.60 |
| Tesla, Inc. | total_assets | 2021 | 62,131 | millions | 19 | Tesla_Annual_Report_2022.pdf | 0.60 |
| Tesla, Inc. | total_assets | 2022 | 82,338 | millions | 19 | Tesla_Annual_Report_2022.pdf | 0.80 |
| Tesla, Inc. | total_equity | 2021 | 30,189 | millions | 19 | Tesla_Annual_Report_2022.pdf | 0.60 |
| Tesla, Inc. | total_equity | 2022 | 44,704 | millions | 19 | Tesla_Annual_Report_2022.pdf | 0.80 |
