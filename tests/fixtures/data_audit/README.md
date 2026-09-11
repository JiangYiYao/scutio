Public responses captured on 2026-09-09 for offline regression tests.

- `tencent_quotes.txt`: `https://qt.gtimg.cn/q=` for the five audited A shares, an ETF, a Beijing stock, Tencent HK and Apple US. Field 6 uses different volume units across boards; field 52 is dynamic PE for A shares.
- `sina_income_items.json`: latest income-statement line items for the five A shares from `CompanyFinanceService.getFinanceReport2022` (`type=0`, report date 2026-06-30). Preserves upstream IDs and hierarchy, including duplicate Chinese titles and nulls.
- `catl_dividends_cninfo.json`: AKShare 1.18.94 `stock_dividend_cninfo(symbol="300750")`. All 13 returned implementation events, including ordinary and special distributions on 2024-04-30 and 2026-04-22, and the special distribution on 2025-01-24.

These are public-source fixtures and contain no credentials. Prices are historical test observations, not current market data.
