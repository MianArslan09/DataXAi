# `online_retail_ii_sample.csv`

18 rows extracted directly from the real UCI Online Retail II dataset
(`data/online_retail_II.xlsx`, both sheets combined = 1,067,371 real rows)
during the Volume 4.1 dataset inspection — not fabricated, not synthetic.
Chosen to cover every characteristic found in that inspection so
automated tests exercise real edge cases:

| Row(s) | Characteristic |
|---|---|
| 1–4 | Normal transactions, UK + non-UK (Germany, France) |
| 5–6 | Null `Customer ID` (22.77% of real dataset) |
| 7 | Null `Description` (0.41% of real dataset) |
| 8–9 | Genuine cancellations (`Invoice` starts with `C`, negative `Quantity`) |
| 10 | Anomaly: cancellation invoice (`C496350`) with **positive** quantity (1 of 19,494 in the real data) |
| 11 | Anomaly: negative `Price` — `StockCode "B"`, "Adjust bad debt" (all 5 negative-price rows in the real data share this exact pattern) |
| 12 | Manual stock adjustment: negative `Quantity`, NOT `C`-prefixed (3,457 such rows exist — a distinct category from customer cancellations) |
| 13–15 | Administrative `StockCode`s mixed into product data: `POST` (postage), `D` (discount), `M` (manual) |
| 16 | `Price = 0` with positive `Quantity` |
| 17 | Common real product (`85123A`) |
| 18 | Exact duplicate of row 1 (deliberate — for duplicate-detection tests) |

Used only for fast automated tests. Real ETL development and
integration runs use the actual file at `data/online_retail_II.xlsx`
(gitignored — see repo root README for how to provide it).
