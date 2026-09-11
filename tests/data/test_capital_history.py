"""Historical partition completeness and financial-unit regressions."""

import pytest
from scutio_data import capital
from scutio_data._providers.akshare import client as akshare_source
from scutio_data._providers.akshare import snapshots as akshare_snapshots
from scutio_data._runtime import timeouts


@pytest.mark.parametrize(
    "code,function,identity,missing",
    [
        ("600519", "stock_margin_detail_sse", "标的证券代码", ("rqye", "rzrqye")),
        ("300750", "stock_margin_detail_szse", "证券代码", ("rzche", "rqchl")),
        ("920992", "stock_margin_detail_bse", "证券代码", ("rzche", "rqchl")),
    ],
)
def test_margin_dates_codes_units_and_market_gaps(monkeypatch, code, function, identity, missing):
    seen = []

    def snapshot(name, **params):
        seen.append((name, params))
        if name == "tool_trade_date_hist_sina":
            return [{"trade_date": day} for day in ("2026-09-04", "2026-09-07", "2026-12-31")], {}
        assert name == function and params["date"] == "20260907"
        row = {
            identity: code,
            "信用交易日期": "20260907",
            "融资余额": 1000000,
            "融资买入额": 0,
            "融券余量": 200,
            "融券卖出量": None,
        }
        return [row, dict(row, **{identity: "000999"})], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.margin_trading(code, 1, end_date="2026-09-07")
    assert out["ok"] and out["partial"]
    row = out["items"][0]
    assert row["date"] == "2026-09-07" and row["rzye"] == 1000000
    assert row["rqyl"] == 200 and row["rqye"] is None and row["rzmre"] == 0
    assert out["coverage"]["missing_fields"] == list(missing)
    assert len(seen) == 2


def test_margin_failed_date_keeps_newer_records_without_skipping_gap(monkeypatch):
    def snapshot(name, **params):
        if name == "tool_trade_date_hist_sina":
            return [{"trade_date": day} for day in ("2026-09-07", "2026-09-08", "2026-09-09")], {}
        if params["date"] == "20260908":
            raise RuntimeError("source unavailable")
        assert params["date"] == "20260909"
        return [{"标的证券代码": "600519", "信用交易日期": "20260909", "融资余额": 10}], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.margin_trading("600519", 3, end_date="2026-09-09")
    assert out["ok"] and out["partial"] and len(out["items"]) == 1
    assert "2026-09-08" in out["coverage"]["failed_partitions"]


@pytest.mark.parametrize("mode", ["empty", "wrong_date", "duplicate"])
def test_invalid_margin_snapshot_is_not_a_zero_balance(monkeypatch, mode):
    def snapshot(name, **params):
        if name == "tool_trade_date_hist_sina":
            return [{"trade_date": "2026-09-09"}], {}
        row = {
            "标的证券代码": "600519",
            "信用交易日期": "20260908" if mode == "wrong_date" else "20260909",
        }
        return ([] if mode == "empty" else [row] * (2 if mode == "duplicate" else 1)), {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.margin_trading("600519", 1, end_date="2026-09-09")
    assert not out["ok"] and out["items"] == []


def test_stale_calendar_does_not_silently_shorten_history(monkeypatch):
    monkeypatch.setattr(
        akshare_snapshots, "fetch_snapshot", lambda *a, **k: ([{"trade_date": "2025-12-31"}], {})
    )
    out = capital.margin_trading("600519", end_date="2026-09-09")
    assert not out["ok"] and "calendar" in out["coverage"]["failed_partitions"]


def test_block_cap_is_split_before_filtering_or_returning(monkeypatch):
    calls = []

    def snapshot(name, **params):
        window = (params["start_date"], params["end_date"])
        calls.append(window)
        if len(calls) == 1:
            return [{"证券代码": "000001"}] * 5000, {}
        # Latest half must be queried first. Identical-looking rows are separate deals.
        assert window[1] == "20260909"
        row = {
            "证券代码": "300750",
            "交易日期": "2026-09-09",
            "收盘价": 100,
            "成交价": 90,
            "成交量": 200000,
            "成交额": 18000000,
            "买方营业部": "机构专用",
        }
        return [row, dict(row)], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.block_trade("300750", 2, end_date="2026-09-09")
    assert out["ok"] and not out["partial"] and len(out["items"]) == 2
    assert out["items"][0]["vol"] == 200000 and out["items"][0]["amount"] == 18000000
    assert out["items"][0]["premium_pct"] == -10 and len(calls) == 2


def test_one_day_block_cap_cannot_be_claimed_complete(monkeypatch):
    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", lambda *a, **k: ([{}] * 5000, {}))
    out = capital.block_trade("300750", 1, end_date="2026-09-09", lookback_days=1)
    assert not out["ok"] and "5000" in out["error"]


def test_empty_block_window_is_not_all_time_no_activity(monkeypatch):
    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", lambda *a, **k: ([], {}))
    out = capital.block_trade("600519", end_date="2026-09-09", lookback_days=31)
    assert out["ok"] and out["partial"] and not out["coverage"]["all_history"]
    assert len(out["coverage"]["queried_partitions"]) == 2


def test_pledge_uses_published_dates_and_keeps_raw_wan_units(monkeypatch):
    dates = []

    def snapshot(name, **params):
        if name == "stock_gpzy_profile_em":
            return [{"交易日期": day} for day in ("2026-08-28", "2026-09-04")], {}
        dates.append(params["date"])
        return [
            {
                "股票代码": "600030",
                "交易日期": params["date"],
                "质押比例": 0.01,
                "质押股数": 80,
                "质押市值": 2239.2,
                "无限售股质押数": 80,
                "限售股质押数": 0,
            }
        ], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.pledge_status("600030", 2, end_date="2026-09-09")
    assert out["ok"] and not out["partial"] and dates == ["20260904", "20260828"]
    row = out["items"][0]
    assert (
        row["pledged_shares_wan"] == 80
        and row["pledged_value_wan"] == 2239.2
        and row["pledge_ratio_pct"] == 0.01
    )
    assert row["restricted_pledged_shares_wan"] == 0


def test_pledge_date_mismatch_rejected(monkeypatch):
    def snapshot(name, **params):
        return (
            [{"交易日期": "2026-09-04"}]
            if name == "stock_gpzy_profile_em"
            else [{"股票代码": "600030", "交易日期": "2026-08-28"}]
        ), {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    assert not capital.pledge_status("600030", 1, end_date="2026-09-09")["ok"]


def test_history_budget_prevents_another_network_query(monkeypatch):
    ticks = [0]
    monkeypatch.setattr(timeouts.time, "monotonic", lambda: ticks[0])
    calls = []

    def snapshot(name, **params):
        calls.append(name)
        assert params["_timeout_seconds"] == 300
        ticks[0] = 301
        return [], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.block_trade("600519", 1, end_date="2026-09-09", lookback_days=60)
    assert not out["ok"] and len(calls) == 1 and "timeout" in out["error"]


def test_shortened_worker_budget_is_not_sent_to_akshare(monkeypatch):
    import json
    from types import SimpleNamespace

    seen = []

    def run(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(stdout='{"ok": true, "items": []}')

    monkeypatch.setattr(akshare_source, "managed_run", run)
    assert akshare_source.fetch("stock_dzjy_mrmx", _timeout_seconds=2, symbol="A股") == []
    assert seen[0]["timeout"] == pytest.approx(2, abs=0.1)
    assert json.loads(seen[0]["input"])["params"] == {"symbol": "A股"}


def _change(direction):
    return {
        "代码": "300750",
        "持股变动信息-增减": direction,
        "持股变动信息-变动数量": 12.5,
        "变动后持股情况-持股总数": 100,
        "变动截止日": "2026-09-09",
        "公告日": "2026-09-10",
    }


def test_shareholder_directions_units_sign_and_partial_failure(monkeypatch):
    def snapshot(function, **params):
        if params["symbol"] == "股东增持":
            raise RuntimeError("increase failed")
        return [_change("减持")], {"snapshot_retrieved_at": 100}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    out = capital.shareholder_changes("300750")
    assert out["ok"] and out["partial"]
    assert out["coverage"]["missing_directions"] == ["增持"]
    row = out["items"][0]
    assert row["shares_changed_wan"] == 12.5 and row["signed_shares_changed_wan"] == -12.5
    assert row["trade_average_price"] is None
    bad = capital.shareholder_changes("300750", direction="invalid")
    assert not bad["ok"]


def test_shareholder_wrong_direction_discards_entire_leg(monkeypatch):
    monkeypatch.setattr(
        akshare_snapshots,
        "fetch_snapshot",
        lambda *a, **k: ([_change("增持"), _change("减持")], {}),
    )
    out = capital.shareholder_changes("300750", direction="increase")
    assert not out["ok"] and out["items"] == []


def test_shareholder_total_share_percentage_is_not_price_change(monkeypatch):
    # Live CATL row: CHANGE_RATE=4.174 is not the ownership percentage;
    # AFTER_CHANGE_RATE=0.013367305472 is the source field mapped by AKShare.
    row = _change("增持")
    row.update({"持股变动信息-占总股本比例": 0.0133673055, "涨跌幅": 4.174})
    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", lambda *a, **k: ([row], {}))
    out = capital.shareholder_changes("300750", direction="increase")
    assert out["ok"]
    assert out["items"][0]["total_share_pct"] == pytest.approx(0.013367305472)
    assert out["items"][0]["total_share_pct"] != 4.174
