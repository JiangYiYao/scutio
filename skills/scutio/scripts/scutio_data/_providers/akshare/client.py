"""Selected AKShare adapters, isolated by a killable total request deadline.

AKShare functions may perform multiple unbounded HTTP calls. A worker process
keeps this behaviour from hanging the caller; no global requests monkeypatch."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from scutio_data._providers.akshare.errors import AKShareError
from scutio_data._providers.akshare.network import network_mode, worker_env
from scutio_data._runtime.timeouts import remaining, source_budget

ALLOWED = frozenset(
    (
        "bond_zh_us_rate",
        "fund_etf_hist_em",
        "futures_foreign_commodity_realtime",
        "index_stock_cons_csindex",
        "index_stock_cons_sina",
        "index_stock_cons_weight_csindex",
        "index_zh_a_hist",
        "macro_china_consumer_goods_retail",
        "macro_china_cpi",
        "macro_china_cpi_yearly",
        "macro_china_enterprise_boom_index",
        "macro_china_exports_yoy",
        "macro_china_fdi",
        "macro_china_fx_gold",
        "macro_china_fx_reserves_yearly",
        "macro_china_gdp",
        "macro_china_gdzctz",
        "macro_china_gyzjz",
        "macro_china_hgjck",
        "macro_china_imports_yoy",
        "macro_china_industrial_production_yoy",
        "macro_china_lpr",
        "macro_china_m2_yearly",
        "macro_china_money_supply",
        "macro_china_national_tax_receipts",
        "macro_china_new_financial_credit",
        "macro_china_non_man_pmi",
        "macro_china_pmi",
        "macro_china_pmi_yearly",
        "macro_china_ppi",
        "macro_china_ppi_yearly",
        "macro_china_reserve_requirement_ratio",
        "macro_china_shrzgm",
        "macro_china_xfzxx",
        "macro_usa_cb_consumer_confidence",
        "macro_usa_cpi_monthly",
        "macro_usa_cpi_yoy",
        "macro_usa_gdp_monthly",
        "macro_usa_ism_non_pmi",
        "macro_usa_ism_pmi",
        "macro_usa_michigan_consumer_sentiment",
        "macro_usa_non_farm",
        "macro_usa_retail_sales",
        "macro_usa_unemployment_rate",
        "news_economic_baidu",
        "news_trade_notify_suspend_baidu",
        "rate_interbank",
        "stock_balance_sheet_by_report_em",
        "stock_board_industry_name_em",
        "stock_cash_flow_sheet_by_report_em",
        "stock_dividend_cninfo",
        "stock_dzjy_mrmx",
        "stock_fhps_detail_em",
        "stock_financial_hk_report_em",
        "stock_financial_us_report_em",
        "stock_ggcg_em",
        "stock_gpzy_pledge_ratio_em",
        "stock_gpzy_profile_em",
        "stock_gsrl_gsdt_em",
        "stock_hk_company_profile_em",
        "stock_hk_daily",
        "stock_hk_hist",
        "stock_hsgt_hist_em",
        "stock_individual_fund_flow",
        "stock_individual_info_em",
        "stock_info_global_cls",
        "stock_info_global_em",
        "stock_irm_cninfo",
        "stock_lhb_detail_em",
        "stock_lhb_jgmmtj_em",
        "stock_lhb_stock_detail_em",
        "stock_margin_detail_bse",
        "stock_margin_detail_sse",
        "stock_margin_detail_szse",
        "stock_market_activity_legu",
        "stock_news_em",
        "stock_profile_cninfo",
        "stock_profit_forecast_ths",
        "stock_profit_sheet_by_report_em",
        "stock_repurchase_em",
        "stock_research_report_em",
        "stock_restricted_release_queue_em",
        "stock_sector_spot",
        "stock_tfp_em",
        "stock_us_daily",
        "stock_us_hist",
        "stock_us_spot_em",
        "stock_value_em",
        "stock_yjkb_em",
        "stock_yysj_em",
        "stock_zh_a_daily",
        "fund_etf_hist_sina",
        "stock_zh_a_disclosure_report_cninfo",
        "stock_zh_a_gdhs_detail_em",
        "stock_zh_a_hist",
        "stock_zh_a_hist_tx",
        "stock_zh_a_spot",
        "stock_zh_a_spot_em",
        "stock_zh_valuation_baidu",
        "tool_trade_date_hist_sina",
    )
)


def fetch(function: str, *, _timeout_seconds: float | None = None, **params) -> list[dict]:
    kind = "batch" if function in ("stock_repurchase_em", "stock_ggcg_em") else None
    with source_budget(kind):
        return _observed_fetch(function, _timeout_seconds=_timeout_seconds, **params)


def _observed_fetch(function, *, _timeout_seconds=None, **params):
    if function not in ALLOWED:
        raise ValueError("unsupported AKShare adapter")
    from scutio_data._providers.akshare import maintenance as akshare_maintenance

    try:
        rows = (
            _fetch(function, **params)
            if _timeout_seconds is None
            else _fetch(function, _timeout_seconds=_timeout_seconds, **params)
        )
    except AKShareError as exc:
        check = akshare_maintenance.observe(function, exc)
        if check:
            exc.update_check = check
            if check.get("state") == "update_available":
                exc.args = (
                    str(exc)
                    + "; newer AKShare available: "
                    + check["latest_compatible"]
                    + " (fix unverified)",
                )
            elif check.get("state") == "pending":
                exc.args = (str(exc) + "; repeated adapter failure: checking AKShare releases",)
        raise
    akshare_maintenance.observe(function)
    return rows


def _fetch(function, *, _timeout_seconds=None, **params):
    if function not in ALLOWED:
        raise ValueError("unsupported AKShare adapter")
    mode = network_mode()
    routes = ["direct"] if mode == "direct" else ["environment"]
    if mode == "auto":
        routes.append("direct")
    attempts = []
    started = time.monotonic()
    budget = remaining("response")
    if _timeout_seconds is not None:
        budget = min(budget, max(0, float(_timeout_seconds)))
    for position, route in enumerate(routes):
        time_left = budget if position == 0 else budget - (time.monotonic() - started)
        if time_left <= 0:
            raise AKShareError("total_timeout", attempts=attempts, timeout_seconds=budget)
        try:
            result = subprocess.run(
                [sys.executable, "-B", str(Path(__file__).with_name("_akshare_worker.py"))],
                input=json.dumps({"function": function, "params": params}),
                text=True,
                capture_output=True,
                timeout=time_left,
                env=worker_env(direct=route == "direct"),
                check=False,
            )
        except subprocess.TimeoutExpired:
            attempts.append({"network": route, "code": "total_timeout"})
            raise AKShareError("total_timeout", attempts=attempts, timeout_seconds=budget) from None
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AKShareError("worker_failed", attempts=attempts) from None
        if not isinstance(payload, dict):
            raise AKShareError("invalid_response", attempts=attempts)
        if payload.get("ok"):
            rows = payload.get("items")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise AKShareError("invalid_response", attempts=attempts)
            return rows
        failure = payload.get("failure") or {}
        code = failure.get("code", "provider_error")
        attempts.append({"network": route, "code": code})
        # Authentication, rate limits, parse errors and generic connection failures
        # do not get a retry. TLS failures may come from proxy interception;
        # retry direct with certificate verification still enabled, within the same budget.
        if mode == "auto" and route == "environment" and code in ("proxy_error", "tls_error"):
            continue
        raise AKShareError(
            code, error_type=failure.get("type"), status=failure.get("status"), attempts=attempts
        )
