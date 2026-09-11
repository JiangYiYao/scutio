"""P0-P2 新数据能力的离线契约测试。"""

from types import SimpleNamespace

import scutio_data._providers.akshare.economic_calendar as calendar_source
import scutio_data._providers.sec as providers_sec
import scutio_data.capital.dividends as capital_dividends
import scutio_data.macro.series as macro_series
import scutio_data.research.consensus as research_consensus
import scutio_data.research.discovery as research_discovery


def _response(data):
    return SimpleNamespace(
        json=lambda: data,
        raise_for_status=lambda: None,
        text="",
        cookies=SimpleNamespace(get_dict=lambda: {}),
    )


def test_trade_calendar_primary_and_historical_backup(monkeypatch):
    from scutio_data import events

    monkeypatch.setattr(
        events,
        "_calendar_local",
        lambda market, start, end: [{"date": "2026-08-05", "market": market}],
    )
    out = events.trade_calendar("a", "20260805", "20260805")
    assert out["ok"] is True
    assert out["backup_used"] is False
    assert out["items"][0]["date"] == "2026-08-05"

    monkeypatch.setattr(
        events, "_calendar_local", lambda *args: (_ for _ in ()).throw(RuntimeError("rules failed"))
    )
    monkeypatch.setattr(
        events,
        "_calendar_from_bars",
        lambda market, start, end: [{"date": "2025-08-05", "market": market}],
    )
    backup = events.trade_calendar("a", "20250805", "20250805")
    assert backup["ok"] is True
    assert backup["backup_used"] is True
    assert backup["partial"] is True


def test_earnings_calendar_failure_is_not_empty_success(monkeypatch):
    from scutio_data import events
    from scutio_data._providers.akshare import snapshots as akshare_snapshots

    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
    )
    out = events.earnings_calendar("20260630", code="600519")
    assert not out["ok"] and out["items"] == []


def test_suspension_and_earnings_mappers_do_not_use_column_order_for_em(monkeypatch):
    from scutio_data.events import map_performance_update_rows, map_suspension_rows

    suspended = map_suspension_rows(
        [
            {
                "SECURITY_CODE": "000001",
                "SUSPEND_START_DATE": "2026-08-05",
                "SUSPEND_REASON": "重大事项",
            }
        ],
        source="em",
    )
    assert suspended[0]["reason"] == "重大事项"
    from scutio_data import events
    from scutio_data._providers.akshare import snapshots as akshare_snapshots

    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda *a, **k: (
            [
                {
                    "股票代码": "600519",
                    "股票简称": "贵州茅台",
                    "首次预约时间": "2026-08-20",
                    "一次变更日期": "2026-08-25",
                }
            ],
            {},
        ),
    )
    earnings = events.earnings_calendar("20260630", code="600519")["items"]
    assert earnings[0]["scheduled_date"] == "2026-08-25"
    assert earnings[0]["report_date"] == "2026-06-30"
    forecasts = map_performance_update_rows(
        [{"SECURITY_CODE": "600519", "PREDICT_AMT_LOWER": 10, "PREDICT_AMT_UPPER": 12}],
        kind="forecast",
    )
    assert forecasts[0]["amount_low"] == 10
    assert forecasts[0]["amount_high"] == 12


def test_suspension_empty_is_unknown_and_baidu_backup_is_partial(monkeypatch):
    from scutio_data import events
    from scutio_data._providers.akshare import snapshots as akshare_snapshots

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", lambda *a, **k: ([], {}))
    primary = events.suspensions("2026-08-05", code="600519")
    assert primary["status"] == "unknown"

    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("em down")),
    )
    monkeypatch.setattr(calendar_source, "calendar_rows", lambda *a, **k: [])
    backup = events.suspensions("2026-08-05", code="600519")
    assert backup["status"] == "unknown"
    assert backup["partial"] is True
    assert backup["coverage"] == "suspension_events_on_date"


def test_market_breadth_counts_and_sina_fallback(monkeypatch):
    from scutio_data import breadth

    mapped = breadth.map_breadth_rows(
        [{"f3": 1.0, "f6": 10}, {"f3": -2.0, "f6": 20}, {"f3": 0, "f6": 5}],
        source="x",
        market="a",
    )
    assert mapped["advancers"] == 1
    assert mapped["decliners"] == 1
    assert mapped["unchanged"] == 1
    assert mapped["amount"] == 35

    monkeypatch.setattr(
        breadth, "_breadth_legulegu_a", lambda *_: (_ for _ in ()).throw(RuntimeError("legu down"))
    )
    monkeypatch.setattr(
        breadth,
        "_breadth_sina_a",
        lambda: {**mapped, "source": "sina_market_snapshot", "complete": True},
    )
    out = breadth.market_breadth("a")
    assert out["ok"] is True
    assert out["backup_used"] is True
    assert out["source"] == "sina_market_snapshot"


def test_index_constituent_official_partial_weights_and_backup(monkeypatch):
    from scutio_data import breadth

    monkeypatch.setattr(
        breadth,
        "_index_csindex",
        lambda code, include: ([{"index_code": code, "code": "600519"}], True, "weight down"),
    )
    out = breadth.index_constituents("000300")
    assert out["ok"] is True
    assert out["partial"] is True
    assert out["weight_error"] == "weight down"

    monkeypatch.setattr(
        breadth, "_index_csindex", lambda *_: (_ for _ in ()).throw(RuntimeError("official down"))
    )
    monkeypatch.setattr(
        breadth, "_index_sina", lambda code: [{"index_code": code, "code": "600519"}]
    )
    backup = breadth.index_constituents("000300")
    assert backup["backup_used"] is True
    assert backup["partial"] is True


def test_consensus_forecast_and_revision_contract(monkeypatch):
    from scutio_data import research

    monkeypatch.setattr(
        research_consensus,
        "eps_forecast",
        lambda *_: {
            "ok": True,
            "error": None,
            "items": [{"年度": 2026, "均值": 2.6, "机构数": 10}],
        },
    )
    out = research.consensus_forecast("600519")
    assert out["ok"] is True
    assert out["backup_used"] is False
    assert out["coverage"]["per_company_years"] is True
    assert out["items"][0]["eps"] == 2.6

    monkeypatch.setattr(
        research_discovery,
        "stock_reports",
        lambda *a, **k: {
            "ok": True,
            "items": [
                {
                    "stockCode": "600519",
                    "forecast_years": [2026],
                    "publishDate": "2026-07-01",
                    "orgSName": "甲",
                    "predictThisYearEps": 2.0,
                },
                {
                    "stockCode": "600519",
                    "forecast_years": [2026],
                    "publishDate": "2026-08-01",
                    "orgSName": "甲",
                    "predictThisYearEps": 2.2,
                },
            ],
        },
    )
    revisions = research.consensus_revisions("600519")
    assert revisions["items"][0]["direction"] == "up"
    assert revisions["items"][0]["change"] == 0.2

    preloaded = {
        "ok": True,
        "items": [
            {
                "stockCode": "600519",
                "forecast_years": [2026],
                "publishDate": "2026-07-01",
                "orgSName": "乙",
                "predictThisYearEps": 1.0,
            },
            {
                "stockCode": "600519",
                "forecast_years": [2026],
                "publishDate": "2026-08-01",
                "orgSName": "乙",
                "predictThisYearEps": 0.9,
            },
        ],
    }
    monkeypatch.setattr(
        research_discovery,
        "stock_reports",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must reuse reports")),
    )
    reused = research.consensus_revisions("600519", reports=preloaded)
    assert reused["items"][0]["direction"] == "down"


def test_structured_corporate_actions_and_partial_aggregation(monkeypatch):
    from scutio_data import capital
    from scutio_data._providers.akshare import snapshots as akshare_snapshots

    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda *a, **k: (
            [
                {
                    "股票代码": "600519",
                    "实施进度": "实施中",
                    "已回购股份数量": 100,
                    "已回购金额": 1000,
                }
            ],
            {},
        ),
    )
    repurchase = capital.share_repurchases("600519")
    assert repurchase["items"][0]["progress"] == "实施中"
    assert repurchase["items"][0]["actual_shares"] == 100

    ok = {"ok": True, "error": None, "items": []}
    bad = {"ok": False, "error": "down", "items": []}
    monkeypatch.setattr(capital, "share_repurchases", lambda *a, **k: ok)
    monkeypatch.setattr(capital, "shareholder_changes", lambda *a, **k: bad)
    monkeypatch.setattr(capital, "pledge_status", lambda *a, **k: ok)
    monkeypatch.setattr(capital_dividends, "dividend_history", lambda *a, **k: ok)
    aggregate = capital.corporate_actions("600519")
    assert aggregate["ok"] is True
    assert aggregate["partial"] is True
    assert aggregate["errors"] == {"holder_changes": "down"}

    monkeypatch.setattr(
        capital,
        "share_repurchases",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must reuse leg")),
    )
    reused = capital.corporate_actions(
        "600519",
        preloaded={
            "repurchases": ok,
            "holder_changes": ok,
            "pledges": ok,
            "dividends": ok,
        },
    )
    assert reused["ok"] is True
    assert reused["partial"] is False


def test_missing_numeric_fields_remain_none(monkeypatch):
    from scutio_data import announcements, capital
    from scutio_data._providers.akshare import snapshots as akshare_snapshots

    def snapshot(function, **params):
        if function == "tool_trade_date_hist_sina":
            return [{"trade_date": "2026-01-05"}], {}
        if function == "stock_margin_detail_sse":
            return [{"信用交易日期": "20260105", "标的证券代码": "600519"}], {}
        return [{"交易日期": "2026-01-05", "证券代码": "600519"}], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        capital_dividends,
        "_dividend_events",
        lambda code: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )

    def fetch(function, **params):
        if function == "stock_zh_a_gdhs_detail_em":
            return [{"代码": "600519", "股东户数统计截止日": "2026-01-02"}]
        if function == "stock_restricted_release_queue_em":
            return [{"解禁时间": "2026-01-02"}, {"解禁时间": "2026-09-02"}]
        return [{"除权除息日": "2026-01-02", "现金分红-现金分红比例": None}]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    assert capital.margin_trading("600519", 1, end_date="2026-01-05")["items"][0]["rzye"] is None
    assert (
        capital.block_trade("600519", 1, end_date="2026-01-05")["items"][0]["premium_pct"] is None
    )
    assert capital.holder_num_change("600519")["items"][0]["holder_num"] is None
    assert capital.dividend_history("600519")["items"][0]["bonus_rmb"] is None

    lockup = announcements.lockup_expiry("600519", "2026-08-05")
    assert lockup["history"][0]["shares"] is None
    assert lockup["upcoming"][0]["ratio"] is None


def test_macro_surprise_and_actual_only_fallback(monkeypatch):
    from scutio_data import macro

    monkeypatch.setattr(
        macro_series,
        "consensus_history",
        lambda name, *a, **k: [{"date": "2026-08-01", "name": name, "value": 3.0, "forecast": 2.5}],
    )
    out = macro.macro_surprises(names=["cpi_yoy"], limit=1)
    assert out["ok"] is True
    assert out["items"][0]["surprise"] == 0.5

    monkeypatch.setattr(
        macro_series,
        "consensus_history",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("j10 down")),
    )
    monkeypatch.setattr(
        macro_series,
        "macro_series",
        lambda *a, **k: {
            "ok": True,
            "items": [{"date": "2026-08-01", "value": 3.0, "prev_value": 2.8}],
        },
    )
    no_fallback = macro.macro_surprises(names=["cpi_yoy"], limit=1)
    assert no_fallback["ok"] is False

    fallback = macro.macro_surprises(names=["cpi_yoy"], limit=1, fallback=True)
    assert fallback["ok"] is True
    assert fallback["partial"] is True
    assert fallback["items"][0]["surprise"] is None
    assert fallback["items"][0]["data_quality"] == "actual_only_fallback"


def test_economic_calendar_supports_bounded_upcoming_window(monkeypatch):
    from scutio_data import macro

    calls = []

    def fake_calendar(day, cate, *, end_day=None):
        assert cate == "economic_data"
        calls.append((day, end_day))
        return [
            {
                "date": value,
                "country": "中国",
                "event": "制造业 PMI",
                "importance": 3,
                "actual": "50.2",
                "forecast": "49.9",
            }
            for value in (day, "2026-08-06", end_day)
        ]

    monkeypatch.setattr(calendar_source, "calendar_rows", fake_calendar)
    out = macro.economic_calendar("2026-08-05", regions=["中国"], min_importance=2, days=3)
    assert out["ok"] is True
    assert out["start_date"] == "2026-08-05"
    assert out["end_date"] == "2026-08-07"
    assert len(out["items"]) == 3
    assert out["items"][0]["surprise"] == 0.3
    assert calls == [("2026-08-05", "2026-08-07")]


def test_macro_snapshot_reuses_preloaded_legs(monkeypatch):
    from scutio_data import macro

    for name in (
        "rates_snapshot",
        "bond_yields_cn_us",
        "fx_usdcny",
        "index_board",
        "commodities_spot",
        "cn_macro_series",
        "us_macro_series",
    ):
        monkeypatch.setattr(
            macro,
            name,
            lambda *a, _name=name, **k: (_ for _ in ()).throw(
                AssertionError("must reuse %s" % _name)
            ),
        )
    series_names = (
        "cpi_yoy",
        "pmi_mfg",
        "pmi_non_mfg",
        "forex_reserves",
        "rrr",
        "social_financing",
        "us_cpi_yoy",
        "us_unemployment",
        "us_nfp",
        "us_ism_pmi",
        "us_fed_funds_upper",
    )
    out = macro.macro_snapshot(
        preloaded={
            "rates_snapshot": {"ok": True, "lpr": {}, "shibor": {}},
            "bond_yields_cn_us": {"ok": True, "items": [{"date": "2026-08-05"}]},
            "fx_usdcny": {"ok": True, "pair": "USDCNY", "price": 7.1},
            "index_board": {"ok": True, "indices": []},
            "commodities_spot": {"ok": True, "gold": {"price": 1}},
            "series": {
                name: {"ok": True, "items": [{"date": "2026-08-01", "value": 1}]}
                for name in series_names
            },
        }
    )
    assert out["ok"] is True
    assert len(out["latest_series"]) == len(series_names)


def test_sec_submissions_shared_by_periodic_and_ownership(monkeypatch):
    from scutio_data import capital

    monkeypatch.setattr(providers_sec, "resolve_us_cik", lambda *_: "0000320193")
    calls = []
    payload = {
        "filings": {
            "recent": {
                "form": ["10-K", "4"],
                "accessionNumber": ["0001-01-01", "0002-01-01"],
                "primaryDocument": ["annual.htm", "owner.xml"],
                "filingDate": ["2026-01-01", "2026-01-02"],
            }
        }
    }

    def fake_get(*args, **kwargs):
        calls.append(args[0])
        return _response(payload)

    monkeypatch.setattr(providers_sec.http, "get", fake_get)
    periodic = providers_sec.periodic_reports_us("usAAPL")
    ownership = capital.ownership_filings("usAAPL")
    assert periodic["ok"] is True
    assert ownership["ok"] is True
    assert len(calls) == 1


def test_us_periodic_all_excludes_current_report_6k(monkeypatch):

    payload = {
        "filings": {
            "recent": {
                "form": ["6-K", "20-F"],
                "accessionNumber": ["0001-01-01", "0002-01-01"],
                "primaryDocument": ["current.htm", "annual.htm"],
                "filingDate": ["2026-08-01", "2026-04-01"],
            }
        }
    }
    monkeypatch.setattr(
        providers_sec,
        "load_submissions",
        lambda code: ("BABA", "0001577552", payload),
    )

    out = providers_sec.periodic_reports_us("usBABA", kind="all", page_size=5)

    assert out["ok"] is True
    assert [item["type"] for item in out["items"]] == ["20-F"]
    assert out["excluded_current_forms"] == ["6-K", "6-K/A"]
