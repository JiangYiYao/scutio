"""Coverage, units and identity checks at newly migrated AKShare boundaries."""

from types import SimpleNamespace

import pytest
from scutio_data import announcements, events, feeds
from scutio_data._providers import eastmoney_events
from scutio_data._providers.akshare import client as akshare_source


def test_news_source_window_is_explicit_after_local_filtering(monkeypatch):
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [])
    out = feeds.stock_news("600519", page_size=50)
    assert out["ok"] and out["partial"] and out["upstream_limit"] == 10
    assert out["empty_reason"] == "upstream_empty" and out["fetched_raw"] == 0


def test_telegraph_combines_date_and_time_and_returns_latest_first(monkeypatch):
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {"标题": "old", "内容": "old text", "发布日期": "2026-09-08", "发布时间": "23:59:00"},
            {"标题": "new", "内容": "new text", "发布日期": "2026-09-09", "发布时间": "09:01:00"},
        ],
    )
    out = feeds.telegraph(1)
    assert out["items"][0]["title"] == "new" and out["items"][0]["time"] == "2026-09-09 09:01:00"
    assert not out["partial"] and out["adapter"] == "akshare"
    assert feeds.telegraph(50)["partial"]


def test_irm_pagination_and_identity_are_preserved(monkeypatch):
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {
                "股票代码": "002594",
                "提问时间": "2026-09-09 10:00:00",
                "问题": "latest",
                "回答内容": None,
            },
            {
                "股票代码": "002594",
                "提问时间": "2026-09-08 10:00:00",
                "问题": "older",
                "回答内容": "reply",
            },
        ],
    )
    out = announcements.irm("002594", page_size=1, page_num=2)
    assert out["ok"] and out["items"][0]["question"] == "older"
    assert out["items"][0]["ask_time"] == "2026-09-08 10:00"
    assert announcements.irm("002594", page_size=1)["items"][0]["answer"] is None
    assert not announcements.irm("600519")["ok"]


def test_irm_rejects_uncovered_exchanges_before_fetch(monkeypatch):
    monkeypatch.setattr(
        akshare_source, "fetch", lambda *a, **k: pytest.fail("must not query Shenzhen source")
    )
    for code in ("600519", "bj920001"):
        out = announcements.irm(code)
        assert not out["ok"] and out["error_code"] == "unsupported_exchange"
        assert out["supported_exchanges"] == ["sz"]


def test_express_preserves_money_units_period_and_missing_metadata(monkeypatch):
    def fetch(function, **params):
        assert function == "stock_yjkb_em" and params["date"] == "20260630"
        return [
            {
                "股票代码": "000001",
                "股票简称": "测试",
                "公告日期": "2026-07-20T00:00:00.000",
                "每股收益": 2.5,
                "营业收入-营业收入": 125000000,
                "净利润-净利润": 30000000,
                "营业收入-同比增长": 10,
                "净利润-同比增长": None,
            },
            {"股票代码": "600519", "营业收入-营业收入": 1},
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = events.performance_updates("2026-06-30", kind="express", code="000001")
    assert out["ok"] and len(out["items"]) == 1
    row = out["items"][0]
    assert row["report_date"] == "2026-06-30" and row["notice_date"] == "2026-07-20"
    assert row["revenue"] == 125000000 and row["net_profit"] == 30000000
    assert row["is_latest"] is None and row["coverage"]["is_latest"] is False


def test_forecast_pagination_uses_metric_tiebreaker_and_preserves_ranges(monkeypatch):
    calls = []

    def get(url, *, params, **kwargs):
        calls.append(dict(params))
        metric = "004" if params["pageNumber"] == "1" else "005"
        payload = {
            "success": True,
            "code": 0,
            "result": {
                "count": 2,
                "pages": 2,
                "data": [
                    {
                        "SECURITY_CODE": "002529",
                        "REPORT_DATE": "2026-06-30 00:00:00",
                        "NOTICE_DATE": "2026-07-15 00:00:00",
                        "PREDICT_FINANCE_CODE": metric,
                        "PREDICT_FINANCE": "归母净利润" if metric == "004" else "扣非净利润",
                        "PREDICT_AMT_LOWER": -20000000,
                        "PREDICT_AMT_UPPER": -10000000,
                        "ADD_AMP_LOWER": -120,
                        "ADD_AMP_UPPER": -110,
                    }
                ],
            },
        }
        return SimpleNamespace(json=lambda: payload)

    monkeypatch.setattr(eastmoney_events.eastmoney, "em_get", get)
    out = events.performance_updates("2026-06-30", kind="forecast", code="002529")
    assert out["ok"] and len(out["items"]) == 2
    assert {row["metric"] for row in out["items"]} == {"归母净利润", "扣非净利润"}
    assert all(row["amount_low"] == -20000000 for row in out["items"])
    assert [call["pageNumber"] for call in calls] == ["1", "2"]
    assert all(call["sortColumns"].endswith(",PREDICT_FINANCE_CODE") for call in calls)
    assert all(call["sortTypes"] == "-1,-1,-1" for call in calls)


@pytest.mark.parametrize(
    "problem", ["duplicate", "missing_row", "wrong_code", "wrong_period", "source_error"]
)
def test_forecast_rejects_incomplete_or_mismatched_source_rows(monkeypatch, problem):
    row = {
        "SECURITY_CODE": "002529",
        "REPORT_DATE": "2026-06-30",
        "NOTICE_DATE": "2026-07-15",
        "PREDICT_FINANCE_CODE": "004",
    }
    if problem == "wrong_code":
        row["SECURITY_CODE"] = "600519"
    if problem == "wrong_period":
        row["REPORT_DATE"] = "2025-06-30"
    payload = {
        "success": True,
        "code": 0,
        "result": {
            "pages": 1,
            "count": 2 if problem in ("duplicate", "missing_row") else 1,
            "data": [row, dict(row)] if problem == "duplicate" else [row],
        },
    }
    if problem == "source_error":
        payload = {"success": False, "code": 9001, "result": None}
    monkeypatch.setattr(
        eastmoney_events.eastmoney,
        "em_get",
        lambda *a, **k: SimpleNamespace(json=lambda: payload),
    )
    out = events.performance_updates("2026-06-30", kind="forecast", code="002529")
    assert not out["ok"] and out["items"] == [] and out["errors"]["forecast"]


def test_forecast_source_empty_remains_a_legal_empty_result(monkeypatch):
    payload = {"success": False, "code": 9201, "result": None, "message": "返回数据为空"}
    monkeypatch.setattr(
        eastmoney_events.eastmoney,
        "em_get",
        lambda *a, **k: SimpleNamespace(json=lambda: payload),
    )
    out = events.performance_updates("2026-06-30", kind="forecast", code="600519")
    assert out["ok"] and out["items"] == []
