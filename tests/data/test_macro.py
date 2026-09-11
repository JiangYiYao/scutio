"""Offline contracts for scutio_data.macro (mocked HTTP; no network required)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import scutio_data.macro.quotes as macro_quotes
import scutio_data.macro.series as macro_series
from scutio_data._providers import quotes as quote_source


def _json_resp(payload):
    resp = SimpleNamespace(
        json=lambda: payload,
        text="",
        status_code=200,
    )
    resp.raise_for_status = lambda: None
    return resp


def test_map_lpr_rows_and_parse_sina_hq():
    from scutio_data._providers.sina_fx import parse_sina_hq
    from scutio_data.macro.series import map_lpr_rows

    rows = map_lpr_rows(
        [
            {
                "TRADE_DATE": "2026-07-20 00:00:00",
                "LPR1Y": 3.0,
                "LPR5Y": 3.5,
                "RATE_1": 4.0,
                "RATE_2": 4.5,
            }
        ]
    )
    assert rows[0]["date"] == "2026-07-20"
    assert rows[0]["lpr_1y"] == 3.0
    assert rows[0]["unit"] == "pct"

    fx = parse_sina_hq(
        'var hq_str_fx_susdcny="10:00:00,7.10,7.09,7.08,0,7.07,7.11,7.06,7.10,在岸人民币,-0.1,0,0,x";',
        "fx_susdcny",
    )
    assert fx is not None
    assert fx["price"] == 7.10
    assert fx["source"] == "sina_hq"

    assert parse_sina_hq('var hq_str_fx_sdxy="10:00,7,7,7,0,7,7,7,7";', "fx_susdcny") is None
    assert parse_sina_hq('var hq_str_hf_GC="2000";', "hf_GC") is None


def test_akshare_bond_units_and_missing_values(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **kw: [
            {"日期": "2026-07-30", "中国国债收益率2年": 1.2, "美国国债收益率10年": 4.1}
        ],
    )
    item = macro.bond_yields_cn_us(1)["items"][0]
    assert item["unit"] == "pct" and item["freq"] == "D"
    assert item["cn_2y"] == 1.2 and item["us_10y"] == 4.1
    assert item["cn_10y"] is None


def test_lpr_history_and_series_use_akshare(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    tables = {
        "macro_china_lpr": [{"TRADE_DATE": "2026-07-20", "LPR1Y": 3}],
        "macro_china_cpi": [{"月份": "2026年06月份", "全国-同比增长": 0.5}],
        "bond_zh_us_rate": [{"日期": "2026-07-20", "中国国债收益率10年": 1.8}],
    }
    monkeypatch.setattr(akshare_source, "fetch", lambda fn, **kw: tables[fn])
    assert macro.lpr_history(1)["items"][0]["lpr_1y"] == 3
    assert macro.cn_macro_series("cpi_yoy", 1)["items"][0]["value"] == 0.5
    assert macro.bond_yields_cn_us(1)["items"][0]["cn_10y"] == 1.8
    assert not macro.cn_macro_series("not_a_series")["ok"]


def test_lpr_failure_envelope(monkeypatch):
    from scutio_data import macro

    def boom(*a, **k):
        raise ConnectionError("down")

    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(akshare_source, "fetch", boom)
    out = macro.lpr_history()
    assert out["ok"] is False
    assert out.get("error")
    assert out.get("items") == []


def test_rates_snapshot_partial(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(fn, **kw):
        if fn == "macro_china_lpr":
            return [{"TRADE_DATE": "2026-07-20", "LPR1Y": 3.1}]
        raise ConnectionError("shibor unavailable")

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    result = macro.rates_snapshot()
    assert result["ok"] and result["partial"] and result["lpr"]["lpr_1y"] == 3.1


def test_fx_and_commodities_sina(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers import sina_fx

    def fake_get(url, headers=None, timeout=12):
        if "fx_susdcny" in url:
            text = (
                'var hq_str_fx_susdcny="12:00:00,7.2,7.1,7.0,0,7.05,7.25,7.0,7.2,在岸人民币,0.1";'
            )
        elif "hf_GC" in url:
            text = 'var hq_str_hf_GC="2100,,2099,2101,2110,2080,12:00,2090,2095,0,1,1,2026-07-30,纽约黄金,0";'
        elif "hf_CL" in url:
            text = 'var hq_str_hf_CL="80.5,,80.4,80.6,81,79,12:00,80,80.2,0,1,7,2026-07-30,纽约原油,0";'
        else:
            text = ""
        return SimpleNamespace(text=text, encoding="gbk")

    monkeypatch.setattr(sina_fx.http, "get", fake_get)

    fx = macro.fx_usdcny()
    assert fx["ok"] is True
    assert fx["price"] == 7.2
    assert fx["pair"] == "USDCNY"

    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda fn, **kw: [{"最新价": 2100 if kw["symbol"] == ["GC"] else 80.5}],
    )
    cmd = macro.commodities_spot()
    assert cmd["ok"] is True
    assert cmd["gold"]["price"] == 2100
    assert cmd["wti"]["price"] == 80.5


def test_macro_snapshot_aggregates_legs(monkeypatch):
    from scutio_data import macro

    monkeypatch.setattr(
        macro,
        "rates_snapshot",
        lambda: {
            "ok": True,
            "error": None,
            "source": "rates_snapshot",
            "lpr": {"date": "2026-07-20", "lpr_1y": 3.0, "lpr_5y": 3.5},
            "shibor": {"on": {"rate": 1.5}},
        },
    )
    monkeypatch.setattr(
        macro,
        "bond_yields_cn_us",
        lambda limit=30: __import__("scutio_data.core", fromlist=["result_list"]).result_list(
            [{"date": "2026-07-30", "cn_10y": 1.7, "us_10y": 4.0, "source": "bond"}],
            source="bond",
        ),
    )
    monkeypatch.setattr(
        macro_quotes,
        "fx_usdcny",
        lambda: {
            "ok": True,
            "error": None,
            "source": "sina",
            "pair": "USDCNY",
            "price": 7.1,
            "name": "在岸人民币",
            "time": "t",
        },
    )
    monkeypatch.setattr(
        macro,
        "index_board",
        lambda codes=None: {
            "ok": True,
            "error": None,
            "source": "tencent",
            "indices": [{"code": "sh000001", "name": "上证", "price": 3000.0, "change_pct": 0.5}],
        },
    )
    monkeypatch.setattr(
        macro_quotes,
        "commodities_spot",
        lambda: {
            "ok": True,
            "error": None,
            "source": "sina",
            "gold": {"price": 2000.0},
            "wti": {"price": 80.0},
        },
    )
    monkeypatch.setattr(
        macro_series,
        "cn_macro_series",
        lambda name, limit=1: __import__("scutio_data.core", fromlist=["result_list"]).result_list(
            [{"date": "2026-06-01", "name": name, "value": 0.5, "unit": "pct"}],
            source="series",
        ),
    )
    monkeypatch.setattr(
        macro_series,
        "us_macro_series",
        lambda name, limit=1: __import__("scutio_data.core", fromlist=["result_list"]).result_list(
            [{"date": "2026-06-01", "name": name, "value": 3.2, "unit": "pct", "market": "US"}],
            source="us_series",
        ),
    )

    snap = macro.macro_snapshot()
    assert snap["ok"] is True
    assert snap["rates"]["lpr"]["lpr_1y"] == 3.0
    assert snap["bonds"]["cn_10y"] == 1.7
    assert snap["fx"]["price"] == 7.1
    assert snap["indices"][0]["code"] == "sh000001"
    assert snap["commodities"]["gold"]["price"] == 2000.0
    assert "cpi_yoy" in snap.get("latest_series", {})
    assert "us_cpi_yoy" in snap.get("latest_series", {})


def test_macro_snapshot_all_fail(monkeypatch):
    from scutio_data import macro
    from scutio_data._runtime.results import result_list_err

    monkeypatch.setattr(macro, "rates_snapshot", lambda: {"ok": False, "error": "r", "source": "x"})
    monkeypatch.setattr(
        macro, "bond_yields_cn_us", lambda limit=30: result_list_err("b", source="x")
    )
    monkeypatch.setattr(
        macro_quotes, "fx_usdcny", lambda: {"ok": False, "error": "f", "source": "x"}
    )
    monkeypatch.setattr(
        macro, "index_board", lambda codes=None: {"ok": False, "error": "i", "source": "x"}
    )
    monkeypatch.setattr(
        macro_quotes, "commodities_spot", lambda: {"ok": False, "error": "c", "source": "x"}
    )
    monkeypatch.setattr(
        macro_series, "cn_macro_series", lambda name, limit=1: result_list_err("s", source="x")
    )
    monkeypatch.setattr(
        macro_series, "us_macro_series", lambda name, limit=1: result_list_err("u", source="x")
    )
    snap = macro.macro_snapshot()
    assert snap["ok"] is False
    assert snap.get("errors")


def test_us_macro_uses_registered_standard_routes(monkeypatch):
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(fn, **kw):
        if fn == "macro_usa_cpi_yoy":
            return [{"时间": "2026-07-01", "发布日期": "2026-08-10", "现值": 2.7}]
        assert fn == "macro_usa_unemployment_rate"
        return [{"日期": "2026-08-01", "今值": 4.1, "预测值": 4.2, "前值": 4}]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = {
        name: macro_series.us_macro_series(name, 1)["items"]
        for name in ("us_cpi_yoy", "us_unemployment")
    }
    assert out["us_cpi_yoy"][0]["value"] == 2.7
    assert out["us_cpi_yoy"][0]["date_basis"] == "observation_period"
    assert out["us_unemployment"][0]["date_basis"] == "release_date"
    assert out["us_unemployment"][0]["value"] == 4.1


def test_index_board_uses_tencent(monkeypatch):
    from scutio_data import macro

    monkeypatch.setattr(
        quote_source,
        "tencent_quote",
        lambda codes: {
            "sh000001": {
                "name": "上证指数",
                "price": 3100.0,
                "change_pct": 1.0,
                "open": 3080.0,
                "last_close": 3070.0,
                "time": "20260911143018",
                "retrieved_at": "2026-09-11T06:30:20+00:00",
            }
        },
    )
    out = macro.index_board(["sh000001"])
    assert out["ok"] is True
    assert out["indices"][0]["price"] == 3100.0
    assert out["indices"][0]["time"] == "20260911143018"
    assert out["indices"][0]["retrieved_at"] == "2026-09-11T06:30:20+00:00"
    assert out["indices"][0]["data_as_of"] is None


def test_index_board_normalizes_requests_and_falls_back_only_for_missing(monkeypatch):
    from scutio_data import macro

    calls = []

    def tencent(codes):
        calls.append(("tencent", codes))
        return {"sh000001": {"name": "上证指数", "price": 3100}}

    def sina(codes):
        calls.append(("sina", codes))
        return {"sz399001": {"name": "深证成指", "price": 10000}}

    monkeypatch.setattr(quote_source, "tencent_quote", tencent)
    monkeypatch.setattr(quote_source, "sina_quote", sina)
    result = macro.index_board(["000001.SH", "sz.399001", "sh000001"])
    assert calls == [
        ("tencent", ["sh000001", "sz399001"]),
        ("sina", ["sz399001"]),
    ]
    assert result["ok"] and not result["partial"] and result["missing"] == []
    assert result["requested_count"] == result["returned_count"] == 2
    assert result["source"] == "multi_source"
    assert result["sources_used"] == ["tencent", "sina"]
    assert [row["source"] for row in result["indices"]] == ["tencent", "sina"]
    assert result["fallback_reason"]["tencent:sz399001"]
    assert result["coverage"]["returned_codes"] == ["sh000001", "sz399001"]


@pytest.mark.parametrize("key", ["000001", "sh000001"])
def test_index_board_rejects_alias_or_conflicting_security_identity(monkeypatch, key):
    from scutio_data import macro, market

    monkeypatch.setattr(
        market,
        "security_quote",
        lambda codes: {
            "ok": True,
            "quotes": {
                key: {"code": "000001", "symbol": "sz000001", "price": 10, "name": "平安银行"}
            },
        },
    )
    result = macro.index_board(["000001.SH"])
    assert not result["ok"] and result["indices"] == []
    assert result["missing"] == ["sh000001"]
    assert result["coverage"]["requested_codes"] == ["sh000001"]


def test_index_board_preserves_missing_and_failed_source(monkeypatch):
    from scutio_data import macro

    monkeypatch.setattr(quote_source, "tencent_quote", lambda codes: {"sh000001": {"price": 3100}})

    def fail(codes):
        raise RuntimeError("Sina unavailable")

    monkeypatch.setattr(quote_source, "sina_quote", fail)
    result = macro.index_board(["000001.SH", "sz399001", "invalid"])
    assert result["ok"] and result["partial"]
    assert result["missing"] == ["invalid", "sz399001"]
    assert result["invalid"] == ["invalid"]
    assert result["requested_count"] == 3 and result["returned_count"] == 1
    assert result["errors"]["sina"] == "Sina unavailable"
    assert result["attempted_sources"] == ["tencent", "sina"]
    assert result["sources_used"] == ["tencent"]
    assert result["coverage"]["missing_codes"] == result["missing"]


def test_index_board_retains_partial_quote_fields(monkeypatch):
    from scutio_data import macro

    monkeypatch.setattr(
        quote_source,
        "tencent_quote",
        lambda codes: {"sh000001": {"price": 3100, "partial": True, "warning": "missing volume"}},
    )
    result = macro.index_board("000001.SH")
    assert result["ok"] and result["partial"] and result["missing"] == []
    assert result["indices"][0]["partial"]
    assert "missing volume" in result["warning"]


@pytest.mark.parametrize("missing", [[], ["sz399001"]])
def test_macro_snapshot_preserves_index_coverage_and_recovered_errors(missing):
    from scutio_data import macro

    index = {
        "ok": True,
        "partial": bool(missing),
        "indices": [{"code": "sh000001", "price": 3100}],
        "missing": missing,
        "source": "tencent",
        "sources_used": ["tencent"],
        "errors": {"sina:sz399001": "missing quote"},
        "coverage": {"missing_codes": missing},
    }
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
    snapshot = macro.macro_snapshot(
        preloaded={
            "rates_snapshot": {"ok": True, "lpr": {"lpr_1y": 3}},
            "bond_yields_cn_us": {"ok": True, "items": [{"cn_10y": 2}]},
            "fx_usdcny": {"ok": True, "price": 7},
            "index_board": index,
            "commodities_spot": {"ok": True, "gold": {"price": 2500}},
            "series": {name: {"ok": True, "items": [{"value": 1}]} for name in names},
        }
    )
    assert snapshot["ok"] and snapshot["partial"] == bool(missing)
    assert snapshot["indices_status"]["missing"] == missing
    assert snapshot["indices_status"]["errors"] == index["errors"]
    assert snapshot["indices_status"]["coverage"] == index["coverage"]
    assert bool(snapshot["errors"]) == bool(missing)


def test_list_macro_series_covers_required_names():
    from scutio_data.macro.series import (
        ALL_MACRO_SERIES,
        MACRO_SERIES,
        US_MACRO_SERIES,
        list_macro_series,
    )

    required_cn = {
        "cpi_yoy",
        "ppi_yoy",
        "pmi_mfg",
        "pmi_non_mfg",
        "m2_yoy",
        "rmb_loan",
        "social_financing",
        "export_yoy",
        "import_yoy",
        "industrial_yoy",
        "retail_yoy",
        "fai_yoy",
        "gdp_yoy",
        "rrr",
        "forex_reserves",
        "fdi",
    }
    required_us = {
        "us_cpi_yoy",
        "us_unemployment",
        "us_nfp",
        "us_ism_pmi",
        "us_fed_funds_upper",
    }
    assert required_cn <= set(MACRO_SERIES)
    assert required_us <= set(US_MACRO_SERIES)
    assert required_cn | required_us <= set(ALL_MACRO_SERIES)
    names = {x["name"] for x in list_macro_series()}
    assert required_cn | required_us <= names
    cn_only = {x["name"] for x in list_macro_series(market="CN")}
    us_only = {x["name"] for x in list_macro_series(market="US")}
    assert required_cn <= cn_only
    assert required_us <= us_only
    assert not (cn_only & us_only)


def test_map_us_indicator_rows_skips_null_value():
    from scutio_data.macro.series import map_us_indicator_rows

    rows = map_us_indicator_rows(
        [
            {
                "REPORT_DATE": "2026-07-01",
                "REPORT_DATE_CH": "2026年07月",
                "VALUE": None,
                "PRE_VALUE": 3.0,
                "INDICATOR_ID": "EMG00000733",
                "INDICATOR_NAME": "美国:CPI",
            },
            {
                "REPORT_DATE": "2026-06-01",
                "REPORT_DATE_CH": "2026年06月",
                "VALUE": 3.5,
                "PRE_VALUE": 3.2,
                "INDICATOR_ID": "EMG00000733",
                "INDICATOR_NAME": "美国:CPI",
            },
        ],
        series_name="us_cpi_yoy",
        limit=5,
    )
    assert len(rows) == 1
    assert rows[0]["value"] == 3.5
    assert rows[0]["market"] == "US"


def test_us_and_macro_series_routing(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source, "fetch", lambda fn, **kw: [{"日期": "2026-08-01", "今值": 4.1, "前值": 4}]
    )
    out = macro.us_macro_series("us_unemployment", limit=1)
    assert out["ok"] is True
    assert out["items"][0]["value"] == 4.1
    routed = macro.macro_series("us_unemployment", limit=1)
    assert routed["ok"] is True
    bad = macro.us_macro_series("not_real")
    assert bad["ok"] is False


def test_map_social_financing_rows_and_sort():
    from scutio_data.macro.series import map_social_financing_rows

    rows = map_social_financing_rows(
        [
            {
                "date": "202603",
                "tiosfs": 52240,
                "rmblaon": 31522,
                "forcloan": 420,
                "entrustloan": -284,
                "trustloan": -173,
                "ndbab": 1258,
                "bibae": 3910,
                "sfinfe": 428,
            },
            {
                "date": "202604",
                "tiosfs": 6245,
                "rmblaon": -4006,
                "forcloan": 184,
                "entrustloan": -283,
                "trustloan": -129,
                "ndbab": -5284,
                "bibae": 4520,
                "sfinfe": 835,
            },
        ],
        limit=10,
    )
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-04-01"  # 新在前
    assert rows[0]["value"] == 6245
    assert rows[0]["rmb_loan"] == -4006
    assert rows[0]["unit"] == "yi_cny"
    assert rows[0]["source"] == "mofcom_shrzgm"
    assert rows[1]["value"] == 52240


def test_social_financing_uses_akshare_mofcom(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda fn, **kw: [
            {
                "月份": "202604",
                "社会融资规模增量": 100,
                "其中-人民币贷款": 80,
                "其中-信托贷款": 0,
                "其中-委托贷款外币贷款": -2,
            }
        ],
    )
    row = macro.cn_macro_series("social_financing", 1)["items"][0]
    assert row["value"] == 100 and row["trust_loan"] == 0 and row["fx_loan"] == -2
    assert row["date"] == "2026-04-01" and row["unit"] == "yi_cny"


def test_jin10_consensus_uses_akshare_and_skips_unpublished_actual(monkeypatch):
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda fn, **kw: [
            {"日期": "2026-07-09", "今值": None},
            {"日期": "2026-06-09", "今值": 0.2, "预测值": 0.15, "前值": 0.1},
        ],
    )
    rows = macro_series.consensus_history("cpi_yoy", limit=2)
    assert len(rows) == 1 and rows[0]["value"] == 0.2 and rows[0]["forecast"] == 0.15
    assert rows[0]["source"] == "akshare_jin10_ec"


def test_map_jin10_ec_rows_skips_null_actual():
    from scutio_data.macro.series import map_jin10_ec_rows

    rows = map_jin10_ec_rows(
        [["2026-01-01", None, 1.0, 0.9], ["2025-12-01", 1.2, 1.1, 1.0]],
        series_name="cpi_yoy",
        limit=5,
    )
    assert len(rows) == 1
    assert rows[0]["value"] == 1.2
    assert rows[0]["prev_value"] == 1.0


def test_sina_fx_uses_latest_price_not_bid_and_retains_source_date(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers import sina_fx

    response = type("Response", (), {})()
    response.text = (
        'var hq_str_fx_susdcny="14:29:28,6.7077,6.7087,6.7161,155,6.7056,'
        "6.7129,6.6974,6.7082,在岸人民币,-0.1176,-0.0079,0.0155,"
        '此行情由新浪财经计算得出,0,0,,2026-09-11";'
    )
    monkeypatch.setattr(sina_fx.http, "get", lambda *a, **k: response)
    result = macro.fx_usdcny()
    assert result["price"] == 6.7082
    assert result["bid"] == 6.7077 and result["ask"] == 6.7087
    assert result["date"] == "2026-09-11" and result["time"] == "14:29:28"
    assert abs((result["price"] / result["prev_close"] - 1) * 100 - result["change_pct"]) < 0.0001


def test_shibor_rate_and_daily_change_have_distinct_units(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(macro, "lpr_history", lambda **kw: {"ok": False, "error": "unused"})
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **kw: [{"报告日": "2026-09-11", "利率": 1.417, "涨跌": 1.3}],
    )
    quote = macro.rates_snapshot()["shibor"]["on"]
    assert quote["rate"] == 1.417 and quote["change"] == 1.3
    assert quote["units"] == {"rate": "pct", "change": "bp"}


def test_jin10_history_selects_latest_published_rows_before_limit(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **kw: [
            {"日期": "1970-01-02", "今值": 15.3, "预测值": None},
            {"日期": "2025-08-01", "今值": 7.3, "预测值": 10.6, "前值": 14.7},
            {"日期": "2025-09-05", "今值": None, "预测值": 7.5, "前值": 7.3},
        ],
    )
    result = macro.macro_surprises(names=["us_nfp"], limit=1)
    assert result["ok"] and result["partial"]
    row = result["items"][0]
    assert row["date"] == "2025-08-01" and row["stale"]
    assert row["actual"] == 73 and row["forecast"] == 106
    assert row["surprise"] == -33 and row["unit"] == "k_persons"


def test_cn_macro_failure_does_not_call_removed_direct_backups(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    def fail(*args, **kwargs):
        raise ConnectionError("unavailable")

    monkeypatch.setattr(akshare_source, "fetch", fail)
    out = macro.cn_macro_series("m2_yoy")
    assert not out["ok"] and "unavailable" in out["error"]


def test_currency_supply_m2_m1_m0_field_mapping(monkeypatch):
    from scutio_data import macro
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda fn, **kw: [
            {
                "月份": "2026年06月份",
                "货币和准货币(M2)-数量(亿元)": 3560000,
                "货币和准货币(M2)-同比增长": 8.5,
                "货币(M1)-数量(亿元)": 1180000,
                "货币(M1)-同比增长": 4,
                "流通中的现金(M0)-数量(亿元)": 120000,
                "流通中的现金(M0)-同比增长": 2,
            }
        ],
    )
    row = macro.cn_macro_series("m2_yoy", 1)["items"][0]
    assert (row["value"], row["m2"], row["m1"], row["m0"]) == (8.5, 3560000, 1180000, 120000)
    assert row["m1_yoy"] == 4 and row["m0_yoy"] == 2
