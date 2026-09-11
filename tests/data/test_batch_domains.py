"""Independent data calls, dependency reuse and domain composition under one batch layer."""

from functools import partial
from threading import Barrier, get_ident

import pytest
from scutio_data import capital, fundamentals, macro, market, valuation
from scutio_data._providers.akshare import client, snapshots
from scutio_data.batch import fetch_many


@pytest.mark.parametrize("code", ["600519", "hk00700", "usAAPL"])
def test_explicit_financial_batch_keeps_market_and_parameters_without_quote(monkeypatch, code):
    seen = []

    def report(symbol, kind, **kwargs):
        seen.append((symbol, kind, kwargs))
        return {"ok": True, "items": [{"report_date": "2025-12-31", "revenue": 10}]}

    def unexpected(*args, **kwargs):
        pytest.fail("unselected quote was fetched")

    monkeypatch.setattr(fundamentals, "financial_report", report)
    monkeypatch.setattr(market, "security_quote", unexpected)
    out = fetch_many(
        {
            "annual_income": partial(
                fundamentals.financial_report, code, "lrb", num=4, period="annual", detail="summary"
            )
        }
    )
    assert out["batch_state"] == "finished"
    assert out["results"]["annual_income"]["result"]["items"][0]["revenue"] == 10
    assert seen == [(code, "lrb", {"num": 4, "period": "annual", "detail": "summary"})]


def test_batch_financial_evidence_survives_quote_failure_and_reuse_does_not_retry(monkeypatch):
    quote = {
        "ok": False,
        "error": "quote unavailable",
        "source": "security_quote",
        "retrieved_at": "2026-09-10T01:00:00Z",
    }
    income = {"ok": True, "items": [{"report_date": "2025-12-31", "revenue": 10}]}
    out = fetch_many(
        {
            "quote": lambda: quote,
            "income": lambda: income,
            "filings": lambda: {"ok": True, "items": []},
        }
    )
    assert out["batch_state"] == "finished"
    assert out["results"]["income"]["result"] is income
    assert out["results"]["quote"]["state"] == "failed"
    assert out["results"]["filings"]["state"] == "success"

    def unexpected(*args, **kwargs):
        pytest.fail("reused quote failure must not trigger another fetch")

    monkeypatch.setattr(valuation, "security_quote", unexpected)
    reused = valuation.valuation_snapshot("600519", quote_env=out["results"]["quote"]["result"])
    assert not reused["ok"] and "quote unavailable" in reused["error"]
    assert quote["retrieved_at"] == "2026-09-10T01:00:00Z"


def test_corporate_actions_runs_independent_missing_legs_and_preserves_supplied_failure(
    monkeypatch,
):
    barrier = Barrier(3)
    threads = set()
    prior = {"ok": False, "error": "repurchases unavailable", "source": "original", "items": []}

    def loader(code, **kwargs):
        threads.add(get_ident())
        barrier.wait(timeout=5)
        return {"ok": True, "items": [{"date": "2026-09-09"}], "source": "fixture"}

    monkeypatch.setattr(
        capital, "share_repurchases", lambda *a, **k: pytest.fail("preloaded leg fetched")
    )
    monkeypatch.setattr(capital, "shareholder_changes", loader)
    monkeypatch.setattr(capital, "pledge_status", loader)
    monkeypatch.setattr(capital.dividends, "dividend_history", loader)
    out = capital.corporate_actions("600519", preloaded={"repurchases": prior})
    assert len(threads) == 3
    assert out["ok"] and out["partial"]
    assert out["errors"] == {"repurchases": "repurchases unavailable"}
    assert out["holder_changes"] == [{"date": "2026-09-09"}]
    assert prior == {
        "ok": False,
        "error": "repurchases unavailable",
        "source": "original",
        "items": [],
    }


def test_nested_domain_batch_stays_on_outer_worker_and_keeps_exception_gap(monkeypatch):
    threads = []

    def loader(code, **kwargs):
        threads.append(get_ident())
        return {"ok": True, "items": []}

    def broken(code, **kwargs):
        threads.append(get_ident())
        raise ValueError("unavailable")

    monkeypatch.setattr(capital, "share_repurchases", loader)
    monkeypatch.setattr(capital, "shareholder_changes", loader)
    monkeypatch.setattr(capital, "pledge_status", broken)
    monkeypatch.setattr(capital.dividends, "dividend_history", loader)
    out = fetch_many({"actions": partial(capital.corporate_actions, "600519")})
    env = out["results"]["actions"]["result"]
    assert len(threads) == 4 and len(set(threads)) == 1
    assert env["ok"] and env["partial"] and env["errors"]["pledges"]


@pytest.mark.parametrize(
    "direction,kinds,fields",
    [
        ("southbound_daily", ("south", "ggt_sh", "ggt_sz"), ("total", "sh", "sz")),
        ("northbound_daily", ("north", "hgt", "sgt"), ("total", "hgt", "sgt")),
    ],
)
def test_connect_detail_parallel_legs_retain_units_and_missing_coverage(
    monkeypatch, direction, kinds, fields
):
    from scutio_data.capital import flows

    barrier = Barrier(3)
    calls = []

    def loader(kind, page_size):
        calls.append((kind, page_size))
        barrier.wait(timeout=5)
        if kind == kinds[2]:
            raise ValueError("upstream unavailable")
        return {
            "ok": True,
            "items": [{"date": "2026-09-09", "buy_amt": None}],
            "units": {"buy_amt": "million_hkd"},
            "coverage": {"buy_amt": False},
        }

    monkeypatch.setattr(flows, "mutual_connect_daily", loader)
    out = getattr(flows, direction)(page_size=2, detail=True)
    assert set(calls) == {(kind, 2) for kind in kinds}
    assert out["ok"] and out["partial"] and out[fields[2]] == []
    assert out["units"] == {"buy_amt": "million_hkd"}
    assert out["coverage"][fields[0]]["buy_amt"] is False


def test_rates_snapshot_independent_tenors_overlap_and_keep_partial_dates_units(monkeypatch):
    keys = macro._SHIBOR_SNAPSHOT_KEYS[:3]
    barrier = Barrier(4)
    monkeypatch.setattr(macro, "_SHIBOR_SNAPSHOT_KEYS", keys)

    def lpr(**kwargs):
        barrier.wait(timeout=5)
        return {"ok": True, "items": [{"date": "2026-08-20", "lpr_1y": 3}]}

    def fetch(name, **kwargs):
        assert name == "rate_interbank"
        barrier.wait(timeout=5)
        if kwargs["indicator"] == macro._SHIBOR_TENORS[keys[-1]]:
            raise RuntimeError("unavailable")
        return [{"报告日": "2026-09-09", "利率": 1.2, "涨跌": 0}]

    monkeypatch.setattr(macro, "lpr_history", lpr)
    monkeypatch.setattr(client, "fetch", fetch)
    out = macro.rates_snapshot()
    assert out["ok"] and out["partial"]
    assert out["lpr"]["date"] == "2026-08-20"
    assert set(out["errors"]) == {"shibor_" + keys[-1]}
    assert out["shibor"][keys[0]]["units"] == {"rate": "pct", "change": "bp"}
    assert out["shibor"][keys[0]]["change"] == 0


def test_macro_snapshot_fetches_only_missing_legs_and_keeps_preloaded_data(monkeypatch):
    names = (
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
    prior = {"ok": True, "items": [{"date": "2026-08-01", "value": 1}], "stale": True}
    supplied = {
        "rates_snapshot": {"ok": False, "error": "rates unavailable"},
        "bond_yields_cn_us": {"ok": True, "items": [{"date": "2026-09-08", "cn_10y": 2}]},
        "index_board": {"ok": True, "indices": []},
        "commodities_spot": {"ok": True, "gold": {"price": 2500}},
        "series": {name: prior for name in names},
    }
    monkeypatch.setattr(
        macro, "rates_snapshot", lambda: pytest.fail("failed preloaded rates refreshed")
    )
    monkeypatch.setattr(
        macro.series, "cn_macro_series", lambda *a, **k: pytest.fail("preloaded series refreshed")
    )
    monkeypatch.setattr(
        macro.series, "us_macro_series", lambda *a, **k: pytest.fail("preloaded series refreshed")
    )
    monkeypatch.setattr(
        macro.quotes, "fx_usdcny", lambda: {"ok": True, "price": 7, "date": "2026-09-09"}
    )
    out = macro.macro_snapshot(preloaded=supplied)
    assert out["ok"] and out["partial"] and out["fx"]["price"] == 7
    assert out["bonds"]["date"] == "2026-09-08"
    assert out["errors"]["rates"] == "rates unavailable"
    assert out["latest_series"]["cpi_yoy"] is prior["items"][0]
    assert out["errors"]["series_cpi_yoy"] == "partial or stale source coverage"


def test_dragon_tiger_parallel_details_wait_for_latest_record_date(monkeypatch):
    barrier = Barrier(3)
    records_returned = False
    calls = []

    def snapshot(name, **kwargs):
        nonlocal records_returned
        calls.append((name, kwargs))
        if name == "stock_lhb_detail_em":
            records_returned = True
            return [{"代码": "000020", "上榜日": "2026-09-08", "上榜原因": "daily"}], {}
        assert records_returned
        barrier.wait(timeout=5)
        if name == "stock_lhb_stock_detail_em":
            assert kwargs["date"] == "20260908" and kwargs["symbol"] == "000020"
            return [{"类型": "daily", "买入金额": 10000}], {"snapshot_retrieved_at": 100}
        assert kwargs["start_date"] == kwargs["end_date"] == "20260908"
        return [{"代码": "000020", "上榜日期": "2026-09-08", "上榜原因": "daily"}], {}

    monkeypatch.setattr(snapshots, "fetch_snapshot", snapshot)
    out = capital.dragon_tiger_board("000020", "2026-09-09")
    assert out["ok"] and not out["partial"]
    assert len(calls) == 4 and len(out["snapshots"]) == 4
    assert out["seats"]["buy"][0]["buy_amt"] == 1


def test_shareholder_direction_snapshots_overlap_and_preserve_units(monkeypatch):
    barrier = Barrier(2)
    calls = []

    def snapshot(name, **kwargs):
        assert name == "stock_ggcg_em"
        label = kwargs["symbol"].removeprefix("股东")
        calls.append(label)
        barrier.wait(timeout=5)
        return [
            {
                "代码": "300750",
                "持股变动信息-增减": label,
                "持股变动信息-变动数量": 12.5,
                "变动截止日": "2026-09-09",
            }
        ], {"snapshot_retrieved_at": 100}

    monkeypatch.setattr(snapshots, "fetch_snapshot", snapshot)
    out = capital.shareholder_changes("300750")
    assert set(calls) == {"增持", "减持"}
    assert out["ok"] and out["coverage"]["completed_directions"] == ["增持", "减持"]
    assert [row["signed_shares_changed_wan"] for row in out["items"]] == [12.5, -12.5]
    assert out["units"]["*_wan"] == "万股"
    assert [entry["snapshot_retrieved_at"] for entry in out["snapshots"]] == [100, 100]


def test_shareholder_timeout_reason_survives_corporate_actions(monkeypatch):
    from scutio_data._providers.akshare.errors import AKShareError
    from scutio_data.capital import history

    captured = []
    failure = AKShareError(
        "total_timeout",
        timeout_seconds=120,
        attempts=[{"network": "environment", "code": "total_timeout"}],
    )

    def snapshot(name, **kwargs):
        assert name == "stock_ggcg_em"
        raise failure

    def batch(calls, **kwargs):
        result = fetch_many(calls, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(snapshots, "fetch_snapshot", snapshot)
    monkeypatch.setattr(history, "fetch_many", batch)
    out = capital.corporate_actions(
        "300750",
        preloaded={
            "repurchases": {"ok": True, "items": []},
            "pledges": {"ok": True, "items": []},
            "dividends": {"ok": True, "items": []},
        },
    )
    assert out["ok"] and out["partial"] and out["holder_changes"] == []
    assert out["errors"]["holder_changes"] == "; ".join([str(failure)] * 2)
    assert "total request timeout (120s)" in out["errors"]["holder_changes"]
    assert len(captured) == 1
    for label in ("增持", "减持"):
        env = captured[0]["results"][label]["result"]
        assert env["error"] == str(failure)
        assert env["error_code"] == "total_timeout"
        assert env["source"] == "eastmoney_holder_change" and env["items"] == []


@pytest.mark.parametrize("code", ["total_timeout", "proxy_error", "rate_limited"])
def test_shibor_safe_failure_reason_survives_rates_snapshot(monkeypatch, code):
    from scutio_data._providers.akshare.errors import AKShareError

    captured = []
    failure = AKShareError(
        code, timeout_seconds=120, status=429 if code == "rate_limited" else None
    )

    def fetch(name, **kwargs):
        assert name == "rate_interbank"
        if kwargs["indicator"] == macro._SHIBOR_TENORS["on"]:
            raise failure
        return [{"报告日": "2026-09-09", "利率": 1.2, "涨跌": 0}]

    def batch(calls, **kwargs):
        result = fetch_many(calls, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(client, "fetch", fetch)
    monkeypatch.setattr(macro, "fetch_many", batch)
    monkeypatch.setattr(
        macro,
        "lpr_history",
        lambda **kwargs: {"ok": True, "items": [{"date": "2026-08-20", "lpr_1y": 3}]},
    )
    out = macro.rates_snapshot()
    assert out["ok"] and out["partial"] and out["lpr"]["lpr_1y"] == 3
    assert out["errors"] == {"shibor_on": str(failure)}
    assert "on" not in out["shibor"]
    env = captured[0]["results"]["on"]["result"]
    assert env["error"] == str(failure)
    assert env["error_code"] == code and env["source"] == "akshare_rate_interbank"


def test_domain_leaf_unknown_exceptions_keep_batch_sanitization(monkeypatch):
    from scutio_data.capital import history

    def unexpected(*args, **kwargs):
        raise RuntimeError("PRIVATE_DIAGNOSTIC_TOKEN")

    monkeypatch.setattr(snapshots, "fetch_snapshot", unexpected)
    monkeypatch.setattr(client, "fetch", unexpected)
    out = fetch_many(
        {
            "holders": partial(history._shareholder_snapshot, "增持"),
            "shibor": partial(macro._shibor_latest, "on"),
        }
    )
    assert out["batch_state"] == "finished"
    for item in out["results"].values():
        assert item["state"] == "failed"
        assert item["result"]["error"] == "RuntimeError"
        assert "PRIVATE_DIAGNOSTIC_TOKEN" not in str(item)
