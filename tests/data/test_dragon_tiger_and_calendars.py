"""Event dates and multiple-reason Dragon Tiger regressions."""

import pytest
from scutio_data import capital, events
from scutio_data._providers.akshare import snapshots as akshare_snapshots


def record(reason="daily", code="000020", net=10001):
    return {
        "代码": code,
        "上榜日": "2026-09-09",
        "上榜原因": reason,
        "龙虎榜净买额": net,
        "龙虎榜买入额": 20000,
        "龙虎榜卖出额": None,
        "收盘价": None,
        "涨跌幅": 0,
        "换手率": None,
    }


def seat(reason="daily", buy=20000):
    return {
        "类型": reason,
        "交易营业部名称": "机构专用",
        "买入金额": buy,
        "卖出金额": None,
        "净额": None,
    }


def institution(reason="daily"):
    return {
        "代码": "000020",
        "上榜日期": "2026-09-09",
        "上榜原因": reason,
        "机构买入总额": 30000,
        "机构卖出总额": 10000,
        "机构买入净额": 20000,
    }


def test_daily_sort_filter_before_rounding_and_nulls(monkeypatch):
    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda *a, **k: ([record(net=None), record(net=10001), record(net=-10000)], {}),
    )
    out = capital.daily_dragon_tiger("2026-09-09", min_net_buy=1.00005)
    assert out["ok"] and out["partial"] and len(out["stocks"]) == 1
    assert out["stocks"][0]["net_buy_wan"] == 1.0
    assert out["stocks"][0]["close"] is None and out["stocks"][0]["sell_wan"] is None
    assert out["stocks"][0]["change_pct"] == 0


def test_multiple_listing_reasons_are_never_summed_or_flattened(monkeypatch):
    def snapshot(name, **params):
        rows = (
            [record("daily"), record("three_days")]
            if name == "stock_lhb_detail_em"
            else [seat("daily"), seat("three_days")]
            if name == "stock_lhb_stock_detail_em"
            else [institution("daily"), institution("three_days")]
        )
        return rows, {"snapshot_retrieved_at": 100, "snapshot_cached": True}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.dragon_tiger_board("000020", "2026-09-09")
    assert out["ok"] and out["partial"]
    assert set(out["seat_groups"]) == {"daily", "three_days"}
    assert out["seats"] == {"buy": [], "sell": []}
    assert out["institution"]["net_amt"] is None and len(out["institution_records"]) == 2
    assert len(out["snapshots"]) == 4


def test_seat_failure_preserves_records_and_other_side(monkeypatch):
    def snapshot(name, **params):
        if params.get("flag") == "卖出":
            raise RuntimeError("sell table down")
        return (
            [record()]
            if name == "stock_lhb_detail_em"
            else [seat()]
            if name == "stock_lhb_stock_detail_em"
            else [institution()]
        ), {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.dragon_tiger_board("000020", "2026-09-09")
    assert out["ok"] and out["partial"] and "sell" in out["errors"]
    assert out["records"][0]["net_buy"] == 1 and out["seats"]["buy"][0]["buy_amt"] == 2
    assert out["institution"]["buy_amt"] == 3 and out["institution"]["net_amt"] == 2


@pytest.mark.parametrize("bad", ["date", "code"])
def test_wrong_lhb_identity_or_date_fails(monkeypatch, bad):
    row = record()
    row["上榜日" if bad == "date" else "代码"] = "2026-09-08" if bad == "date" else ""
    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", lambda *a, **k: ([row], {}))
    assert not capital.daily_dragon_tiger("2026-09-09")["ok"]


def test_no_lhb_records_does_not_fetch_seats(monkeypatch):
    calls = []

    def snapshot(name, **params):
        calls.append(name)
        return [], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.dragon_tiger_board("600519", "2026-09-09")
    assert out["ok"] and out["records"] == [] and out["institution"]["net_amt"] is None
    assert calls == ["stock_lhb_detail_em"]


def test_empty_seats_not_reported_as_complete(monkeypatch):
    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda name, **p: ([record()] if name == "stock_lhb_detail_em" else [], {}),
    )
    out = capital.dragon_tiger_board("000020", "2026-09-09")
    assert out["ok"] and out["partial"] and set(out["errors"]) == {"buy", "sell"}


@pytest.mark.parametrize(
    "start,expected,status",
    [
        ("2026-09-15", None, "scheduled"),
        ("2026-09-08", None, "suspended"),
        ("2026-09-08", "2026-09-09", "unknown"),
    ],
)
def test_future_suspension_and_expected_resumption_are_not_current_status(
    monkeypatch, start, expected, status
):
    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda *a, **k: (
            [
                {
                    "代码": "601995",
                    "停牌时间": start,
                    "预计复牌时间": expected,
                    "停牌原因": "重要公告",
                }
            ],
            {},
        ),
    )
    out = events.suspensions("2026-09-09", code="601995")
    assert out["ok"] and out["status"] == status and out["partial"]
    assert out["items"][0]["resume_date_basis"] == "expected"


def test_calendar_changes_use_last_revision_not_largest_date(monkeypatch):
    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda *a, **k: (
            [
                {
                    "股票代码": "600519",
                    "首次预约时间": "2026-08-20",
                    "一次变更日期": "2026-08-29",
                    "二次变更日期": "2026-08-25",
                    "实际披露时间": "2026-08-24",
                }
            ],
            {},
        ),
    )
    out = events.earnings_calendar("20260630", code="600519")
    row = out["items"][0]
    assert (
        out["ok"] and row["scheduled_date"] == "2026-08-25" and row["actual_date"] == "2026-08-24"
    )
    assert row["change_dates"] == ["2026-08-29", "2026-08-25"]
    assert out["report_date_basis"] == "requested_period"


def test_calendar_beijing_segment_and_partial_market_failure(monkeypatch):
    seen = []

    def snapshot(name, **params):
        seen.append(params["symbol"])
        if params["symbol"] == "沪深A股":
            raise RuntimeError("market down")
        return [{"股票代码": "920992", "首次预约时间": "2026-08-28"}], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = events.earnings_calendar("20260630")
    assert out["ok"] and out["partial"] and out["coverage"]["missing_segments"] == ["沪深A股"]
    seen.clear()
    out = events.earnings_calendar("20260630", code="920992")
    assert out["ok"] and seen == ["京市A股"]


def test_invalid_reporting_period_fails_without_fetch(monkeypatch):
    monkeypatch.setattr(
        akshare_snapshots, "fetch_snapshot", lambda *a, **k: pytest.fail("unexpected request")
    )
    assert not events.earnings_calendar("20260629")["ok"]
