"""Data-quality regressions at migrated provider boundaries (no HTTP)."""

import pytest
from scutio_data import breadth, fundamentals, market, research
from scutio_data._providers.akshare import client as akshare_source


def test_index_weights_disclose_missing_rows_and_their_own_date(monkeypatch):
    def fetch(function, **params):
        if function == "index_stock_cons_csindex":
            return [
                {"成分券代码": "600519", "日期": "2026-09-09"},
                {"成分券代码": "000001", "日期": "2026-09-09"},
            ]
        return [{"成分券代码": "600519", "权重": 2.5, "日期": "2026-08-31"}]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = breadth.index_constituents("000300")
    assert out["ok"] and out["partial"] and out["weight_error"]
    assert out["items"][0]["date"] == "2026-09-09"
    assert out["items"][0]["weight_date"] == "2026-08-31"
    assert out["items"][1]["weight_pct"] is None


def test_market_width_does_not_count_missing_changes_as_unchanged(monkeypatch):
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {"涨跌幅": 2, "成交额": 100},
            {"涨跌幅": -1, "成交额": 200},
            {"涨跌幅": None, "成交额": None},
        ],
    )
    out = breadth.market_breadth("us")
    assert out["ok"] and out["partial"]
    assert out["total"] == 2 and out["upstream_total"] == 3
    assert out["advancers"] == out["decliners"] == 1 and out["unchanged"] == 0
    assert out["amount"] == 300


def test_legu_reports_source_date_and_missing_auxiliary_fields(monkeypatch):
    values = {
        "上涨": 1728,
        "下跌": 3369,
        "平盘": 110,
        "停牌": 12,
        "统计日期": "2026-09-09 15:00:00",
    }
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [{"item": key, "value": value} for key, value in values.items()],
    )
    out = breadth.market_breadth()
    assert out["ok"] and out["total"] == 5207 and out["limit_up"] is None
    assert out["as_of"] == "2026-09-09 15:00:00"
    assert out["partial"] and not out["complete"]
    assert out["coverage"]["universe"] == "sse_szse_a_shares"
    assert out["coverage"]["missing_exchanges"] == ["bj"]
    assert not out["coverage"]["suspended_in_total"] and out["suspended"] == 12
    assert out["advance_pct"] == round(1728 / 5207 * 100, 4)


@pytest.mark.parametrize("code", ["hk00700", "usAAPL"])
def test_financials_reject_wrong_identity_from_akshare(monkeypatch, code):
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [{"SECURITY_CODE": "WRONG"}])
    result = fundamentals.financial_report(code, sources=["akshare"])
    assert not result["ok"] and "identity mismatch" in result["error"]


def test_report_filtering_keeps_forecast_year_and_pdf_identity(monkeypatch):
    rows = [
        {
            "股票代码": "600519",
            "股票简称": "贵州茅台",
            "日期": day,
            "报告名称": "研究报告",
            "报告PDF链接": "https://pdf.dfcfw.com/pdf/H3_AP12345_1.pdf",
            "2026-盈利预测-收益": 10,
            "2026-盈利预测-市盈率": None,
            "2027-盈利预测-收益": 12,
        }
        for day in ("2025-01-01", "2026-09-08")
    ]
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: rows)
    out = research.stock_reports("600519", begin="2026-01-01")
    assert out["ok"] and len(out["items"]) == 1
    row = out["items"][0]
    assert row["infoCode"] == "AP12345" and row["forecast_years"] == [2026, 2027]
    assert row["predictNextYearEps"] == 12 and row["predictThisYearPe"] is None
    assert out["coverage"]["authors"] is False


def test_dividends_keep_verified_days_when_other_day_and_backup_fail(monkeypatch):
    from scutio_data import capital

    def fetch(function, **params):
        if function == "stock_dividend_cninfo":
            return [
                {"除权日": "2025-01-24", "实施方案分红说明": "10派12.3元(含税)"},
                {"除权日": "2024-04-30", "实施方案分红说明": "unrecognized plan"},
            ]
        raise RuntimeError("regular source down")

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = capital.dividend_history("300750")
    assert out["ok"] and out["partial"] and out["source"] == "akshare_cninfo"
    assert out["items"][0]["bonus_rmb"] == 12.3 and len(out["items"]) == 1
    assert not out["special_dividend_coverage"]["available"]
    assert (
        "Unparsed dividend plan on 2024-04-30" in out["warning"]
        and "regular source down" in out["warning"]
    )


@pytest.mark.parametrize("market_key", ["hk", "h"])
def test_hk_breadth_is_unavailable_without_attempting_disabled_source(monkeypatch, market_key):
    from unittest.mock import Mock

    fetch = Mock()
    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = breadth.market_breadth(market_key)
    assert not out["ok"] and out["unavailable"] and out["reason"] == "source_disabled"
    assert out["market"] == "hk" and "total" not in out
    fetch.assert_not_called()


@pytest.mark.parametrize("missing", [None, "", "-", float("nan"), float("inf"), True])
def test_invalid_bar_price_reaches_quality_check_and_falls_back(monkeypatch, missing):
    calls = []
    good = {"date": "2026-09-10", "open": 10, "high": 11, "low": 9, "close": 10.5}

    def fetch(function, **params):
        calls.append(function)
        return [{**good, "low": missing}] if function == "stock_zh_a_daily" else [good]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = market.security_bars("600519", count=1, sources=["akshare_sina", "akshare_tencent"])
    assert out["ok"] and not out["partial"] and out["source"] == "akshare_tencent"
    assert out["bars"][0]["low"] == 9
    assert "invalid_ohlc_rows:1" in out["errors"]["akshare_sina"]
    assert calls == ["stock_zh_a_daily", "stock_zh_a_hist_tx"]


def test_all_missing_bar_prices_cannot_become_a_successful_zero_bar(monkeypatch):
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {"date": "2026-09-10", "open": None, "high": None, "low": None, "close": None}
        ],
    )
    out = market.security_bars("600519", count=1, sources=["akshare_sina"])
    assert not out["ok"] and not out["bars"]
    assert "invalid_ohlc_rows:1" in out["error"]


def test_bar_quality_rejects_nonfinite_prices_even_without_akshare_normalization(monkeypatch):
    monkeypatch.setattr(
        market.hithink,
        "bars",
        lambda *a, **k: [
            {"datetime": "2026-09-10", "open": 10, "high": float("inf"), "low": 9, "close": 10}
        ],
    )
    out = market.security_bars("600519", count=1, sources=["hithink"])
    assert not out["ok"] and "invalid_ohlc_rows:1" in out["error"]


def test_native_adjusted_bar_prices_may_be_negative(monkeypatch):
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [{"date": "2026-09-10", "open": -2, "high": -1, "low": -3, "close": -1.5}],
    )
    out = market.security_bars("600519", count=1, adjust="qfq", sources=["akshare_sina"])
    assert out["ok"] and out["bars"][0]["close"] == -1.5
