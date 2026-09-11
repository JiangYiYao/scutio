"""Research scope is enforced before source I/O, while retained paths stay callable."""

import importlib.util

import pytest
from scutio_data import capital, data_sources, feeds, market
from scutio_data._providers.akshare import client
from scutio_data._providers.akshare import market as akshare_market


@pytest.mark.parametrize("code", ["600519", "hk00700", "usAAPL"])
@pytest.mark.parametrize("frequency", ["1m", "5m", "15m", "30m", "60m", "1H"])
def test_intraday_requests_are_rejected_before_source_selection(monkeypatch, code, frequency):
    def forbidden(*args, **kwargs):
        pytest.fail("out-of-scope frequency must not reach a provider")

    monkeypatch.setattr(market.hithink, "preferred", forbidden)
    monkeypatch.setattr(market.hithink, "bars", forbidden)
    monkeypatch.setattr(akshare_market, "bars", forbidden)
    for sources in (None, ("hithink",), ("akshare_eastmoney",)):
        with pytest.raises(ValueError, match="frequency"):
            market.security_bars(code, frequency, sources=sources)


@pytest.mark.parametrize(
    "function",
    [
        "fund_etf_hist_min_em",
        "index_zh_a_hist_min_em",
        "stock_hk_hist_min_em",
        "stock_us_hist_min_em",
        "stock_zh_a_hist_min_em",
        "stock_zh_a_minute",
        "stock_hsgt_fund_min_em",
        "stock_hot_keyword_em",
        "stock_hot_rank_em",
        "stock_zt_pool_dtgc_em",
        "stock_zt_pool_em",
        "stock_zt_pool_previous_em",
        "stock_zt_pool_zbgc_em",
        "option_sse_codes_sina",
        "option_sse_greeks_sina",
        "option_sse_list_sina",
        "option_sse_spot_price_sina",
    ],
)
def test_removed_akshare_capabilities_never_start_worker(monkeypatch, function):
    def forbidden(*args, **kwargs):
        pytest.fail("removed capability started a worker")

    monkeypatch.setattr(client, "managed_run", forbidden)
    with pytest.raises(ValueError, match="unsupported AKShare adapter"):
        client.fetch(function)


def test_only_research_relevant_direct_adapters_are_advertised():
    adapters = data_sources.status()["direct_adapters"]
    assert {row["id"] for row in adapters} == {
        "quotes",
        "us_macro",
        "fx_timestamp",
        "forecast_ranges",
        "concept_membership",
        "non_stock_research",
        "sec_filings",
    }
    assert all(row["status"] == "retained" for row in adapters)


def test_removed_public_modules_and_functions_have_no_compatibility_stubs():
    for name in (
        "options",
        "sentiment",
        "_providers.finra",
        "_providers.guba",
        "_providers.ths_capital",
        "_providers.ths_sentiment",
    ):
        assert importlib.util.find_spec("scutio_data." + name) is None
    for name in (
        "hsgt_realtime",
        "connect_flow_rtmin",
        "fund_flow_minute",
        "hot_reason",
        "short_activity",
    ):
        assert not hasattr(capital, name)
    assert not hasattr(feeds, "guba_posts")
    for name in (
        "dragon_tiger_board",
        "daily_dragon_tiger",
        "margin_trading",
        "block_trade",
        "pledge_status",
        "northbound_daily",
        "southbound_daily",
        "ownership_filings",
    ):
        assert callable(getattr(capital, name))


@pytest.mark.parametrize("frequency,period", [("D", "daily"), ("W", "weekly"), ("M", "monthly")])
@pytest.mark.parametrize(
    "code,function",
    [
        ("600519", "stock_zh_a_hist"),
        ("sh000001", "index_zh_a_hist"),
        ("510300", "fund_etf_hist_em"),
        ("hk00700", "stock_hk_hist"),
        ("usAAPL", "stock_us_hist"),
    ],
)
def test_daily_weekly_monthly_bars_keep_asset_specific_routes(
    monkeypatch, code, function, frequency, period
):
    calls = []

    def fetch(name, **params):
        calls.append((name, params))
        return [
            {"日期": "2026-09-08", "开盘": 10, "收盘": 11, "最高": 12, "最低": 9, "成交量": 100}
        ]

    monkeypatch.setattr(client, "fetch", fetch)
    result = market.security_bars(code, frequency, count=1, sources=("akshare_eastmoney",))
    assert result["ok"], result
    assert len(calls) == 1 and calls[0][0] == function
    assert calls[0][1]["period"] == period
    assert result["bars"][0]["close"] == 11
