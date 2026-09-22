"""HK/US quotes, bars, stock_info — offline tests driving shipped entry points."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import scutio_data._providers.eastmoney as providers_eastmoney
from scutio_data._providers import quotes as quote_source
from scutio_data._providers.akshare import market as akshare_market


def _eastmoney_payload(code="00700", **updates):
    return {
        "f57": code,
        "f58": "Example",
        "f43": 12,
        "f60": 10,
        "f46": 11,
        "f44": 13,
        "f45": 10,
        "f47": 100,
        "f48": 1200,
        "f169": 2,
        "f170": 20,
        **updates,
    }


def _mock_eastmoney_payloads(monkeypatch, payloads):
    def get(url, *, params, **kwargs):
        payload = payloads[params["secid"].split(".", 1)[1]]
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(json=lambda: {"data": payload})

    monkeypatch.setattr(providers_eastmoney, "em_get", get)


@pytest.mark.parametrize("price", [None, "", "-", "invalid", "NaN", "inf", 0, -1])
def test_eastmoney_missing_price_preserves_last_close_as_partial(monkeypatch, price):
    from scutio_data import market

    _mock_eastmoney_payloads(monkeypatch, {"00700": _eastmoney_payload(f43=price)})
    result = market.security_quote(["hk00700"], sources=("eastmoney",))
    row = result["quotes"]["hk00700"]
    assert result["ok"] and result["partial"]
    assert row["price"] is None and row["last_close"] == 10
    assert row["missing_fields"] == ["price"]
    assert row["coverage"]["price"] is False and "price" in result["warning"]


def test_eastmoney_missing_numeric_fields_stay_unknown(monkeypatch):
    fields = (
        "f60",
        "f46",
        "f44",
        "f45",
        "f47",
        "f48",
        "f169",
        "f170",
        "f168",
        "f50",
        "f116",
        "f117",
    )
    payload = _eastmoney_payload(**dict.fromkeys(fields, "-"))
    _mock_eastmoney_payloads(monkeypatch, {"00700": payload})
    row = quote_source.eastmoney_quote(["hk00700"])["hk00700"]
    assert row["price"] == 12 and row["partial"]
    for field in (
        "last_close",
        "open",
        "high",
        "low",
        "volume",
        "amount",
        "amount_wan",
        "change_amt",
        "change_pct",
        "turnover_pct",
        "vol_ratio",
        "mcap_yi",
        "float_mcap_yi",
        "amplitude_pct",
    ):
        assert row[field] is None, field


def test_eastmoney_real_zero_turnover_and_change_are_preserved(monkeypatch):
    from scutio_data import market

    payload = _eastmoney_payload(f47=0, f48=0, f169=0, f170=0, f168=0, f50=0)
    _mock_eastmoney_payloads(monkeypatch, {"00700": payload})
    result = market.security_quote(["hk00700"], sources=("eastmoney",))
    row = result["quotes"]["hk00700"]
    assert result["ok"] and not result["partial"]
    for field in (
        "volume",
        "amount",
        "amount_wan",
        "change_amt",
        "change_pct",
        "turnover_pct",
        "vol_ratio",
    ):
        assert row[field] == 0


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("bad", ["missing_price", "malformed_payload", "request_error"])
def test_eastmoney_bad_security_does_not_discard_good_rows(monkeypatch, reverse, bad):
    from scutio_data import market

    payload = {
        "missing_price": _eastmoney_payload("00941", f43="-", f60=None),
        "malformed_payload": ["invalid"],
        "request_error": RuntimeError("synthetic request failure"),
    }[bad]
    _mock_eastmoney_payloads(monkeypatch, {"00700": _eastmoney_payload(), "00941": payload})
    codes = ["hk00700", "hk00941"]
    result = market.security_quote(codes[::-1] if reverse else codes, sources=("eastmoney",))
    assert result["ok"] and result["partial"]
    assert result["quotes"]["hk00700"]["price"] == 12
    assert result["returned_count"] == 1 and result["missing"] == ["hk00941"]
    assert "eastmoney:hk00941" in result["errors"]


def test_eastmoney_failed_security_alone_uses_next_source(monkeypatch):
    from scutio_data import market

    _mock_eastmoney_payloads(
        monkeypatch,
        {
            "00700": _eastmoney_payload(),
            "00941": _eastmoney_payload("00941", f43="-", f60="-"),
        },
    )
    requested = []

    def fallback(codes):
        requested.extend(codes)
        return {"hk00941": {"symbol": "hk00941", "code": "00941", "price": 20}}

    monkeypatch.setattr(quote_source, "tencent_quote", fallback)
    result = market.security_quote(["hk00700", "hk00941"], sources=("eastmoney", "tencent"))
    assert result["ok"] and not result["partial"]
    assert result["returned_count"] == 2 and requested == ["hk00941"]
    assert result["quotes"]["hk00700"]["source"] == "eastmoney"
    assert result["quotes"]["hk00941"]["source"] == "tencent"


def test_eastmoney_total_failure_retains_request_error(monkeypatch):
    from scutio_data import market

    _mock_eastmoney_payloads(monkeypatch, {"00700": RuntimeError("synthetic request failure")})
    result = market.security_quote(["hk00700"], sources=("eastmoney",))
    assert not result["ok"]
    assert "synthetic request failure" in result["error"]


@pytest.mark.parametrize("code,currency", [("hk00700", "CNY"), ("usAAPL", "USD")])
@pytest.mark.parametrize("provided", [False, True])
def test_financial_report_discloses_missing_currency_without_guessing(
    monkeypatch, code, currency, provided
):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client

    raw = {
        "SECURITY_CODE": code[2:],
        "REPORT_DATE": "2025-12-31",
        "STD_ITEM_NAME": "营业额",
        "ITEM_NAME": "营业收入",
        "AMOUNT": 123,
    }
    if provided:
        raw["CURRENCY"] = currency
    monkeypatch.setattr(client, "fetch", lambda *args, **kwargs: [raw])
    result = fundamentals.financial_report(code, num=1)
    assert result["ok"]
    assert result["partial"] is (not provided)
    assert result["missing_fields"] == ([] if provided else ["currency"])
    assert result["items"][0].get("币种") == (currency if provided else None)
    assert ("report currency" in (result["warning"] or "")) is (not provided)
    if not provided:
        short = fundamentals.financial_report(code, num=2)
        assert "Only 1 of 2" in short["warning"] and "report currency" in short["warning"]


# Realistic Tencent quote lines (truncated field count but enough for parser).
_TENCENT_HK = (
    'v_hk00700="100~腾讯控股~00700~471.800~466.400~466.400~31791979.0~0~0~471.800'
    "~0~0~0~0~0~0~0~0~0~471.800~0~0~0~0~0~0~0~0~0~31791979.0~2026/07/30 16:08:28"
    "~5.400~1.16~475.000~462.800~471.800~31791979.0~14947725389.176~0~17.23~~0~0"
    '~2.62~42898.4919~42898.4919~TENCENT~1.13~677.700~411.000~1.38~-21.45~0~0~0~0~0~14.40~3.00";'
)
_TENCENT_US = (
    'v_usAAPL="200~苹果~AAPL.OQ~338.19~340.08~339.73~56090840~0~0~340.00~520~0~0~0~0'
    "~0~0~0~0~340.10~80~0~0~0~0~0~0~0~0~~2026-07-29 16:00:01~-1.89~-0.56~344.57"
    "~337.35~USD~56090840~19115587094~0.38~40.94~~45.33~~2.12~49640.49163~49671.16926"
    '~Apple Inc.~8.26~344.57~200.72~440~46.64~0.31~49671.16926~24.62~3.77~GP";'
)
_TENCENT_A = (
    'v_sh600519="1~贵州茅台~600519~1500.00~1490.00~1495.00~10000~0~0~1500~0~0~0~0~0~0'
    "~0~0~0~1500~0~0~0~0~0~0~0~0~0~0~20260101150000~10.00~0.67~1510.00~1480.00~0~0"
    "~150000~1.00~30.00~0~0~0~2.00~18000~20000~8.50~1650.00~1350.00~1.00~0~25.00"
    '~";'
)

_SINA_HK = (
    'var hq_str_rt_hk00700="TENCENT,腾讯控股,466.400,466.400,475.000,462.800,471.800,'
    "5.400,1.158,471.600,471.800,14947725389.176,31791979,17.154,0.000,675.134,"
    '411.000,2026/07/30,16:08:28,100|0";\n'
)
_SINA_US = (
    'var hq_str_gb_aapl="苹果,338.1900,-0.56,2026-07-30 20:29:51,-1.8900,339.7300,'
    "344.5699,337.3501,342.8900,200.4500,56090797,50582456,4967116925640,8.30,"
    '40.750000,0.00,0.00,0.00,0.00,14687356000";\n'
)


def _assert_amount_pair(row, *, expect_amount=None, expect_wan=None):
    """amount=本币元，amount_wan=本币万元，恒有 amount ≈ amount_wan * 10000。"""
    amount = float(row.get("amount") or 0)
    wan = float(row.get("amount_wan") or 0)
    if expect_amount is not None:
        assert amount == pytest.approx(expect_amount, rel=1e-6, abs=0.01)
    if expect_wan is not None:
        assert wan == pytest.approx(expect_wan, rel=1e-6, abs=0.01)
    if amount or wan:
        assert amount == pytest.approx(wan * 10000.0, rel=1e-6, abs=1.0)


def test_amount_pair_helper():
    from scutio_data._providers.quote_parse import amount_pair

    a, w = amount_pair(150000, raw_unit="wan")
    assert w == 150000.0
    assert a == 1_500_000_000.0
    a2, w2 = amount_pair(14947725389.176, raw_unit="yuan")
    assert a2 == pytest.approx(14947725389.176)
    assert w2 == pytest.approx(1494772.5389)
    assert amount_pair(0) == (0.0, 0.0)
    assert amount_pair(None) == (0.0, 0.0)


def test_parse_tencent_quote_raw_hk_us_a():
    from scutio_data._providers.quote_parse import parse_tencent_quote_raw

    rows = parse_tencent_quote_raw(_TENCENT_HK + _TENCENT_US + _TENCENT_A)
    assert "hk00700" in rows
    assert rows["hk00700"]["price"] == pytest.approx(471.8)
    assert rows["hk00700"]["currency"] == "HKD"
    assert rows["hk00700"]["limit_up"] is None
    assert rows["hk00700"]["mcap_yi"] == pytest.approx(42898.4919)
    assert rows["hk00700"]["float_mcap_yi"] == pytest.approx(42898.4919)
    assert rows["hk00700"]["name_en"] == "TENCENT"
    assert rows["hk00700"]["vol_ratio"] == pytest.approx(1.38)
    assert rows["hk00700"]["pb"] == pytest.approx(3.0)
    assert rows["hk00700"]["vol_ratio"] != pytest.approx(411.0)
    # 腾讯港股：源字段元 → amount 元、amount_wan 万
    _assert_amount_pair(rows["hk00700"], expect_amount=14947725389.176)

    assert "usAAPL" in rows
    assert rows["usAAPL"]["price"] == pytest.approx(338.19)
    assert rows["usAAPL"]["currency"] == "USD"
    assert rows["usAAPL"]["code"] == "AAPL"
    assert rows["usAAPL"]["mcap_yi"] == pytest.approx(49671.16926)
    assert rows["usAAPL"]["float_mcap_yi"] == pytest.approx(49640.49163)
    assert rows["usAAPL"]["float_mcap_yi"] <= rows["usAAPL"]["mcap_yi"]
    _assert_amount_pair(rows["usAAPL"], expect_amount=19115587094.0)

    assert "sh600519" in rows
    assert rows["sh600519"]["currency"] == "CNY"
    assert rows["sh600519"]["mcap_yi"] == pytest.approx(20000.0)
    assert rows["sh600519"]["float_mcap_yi"] == pytest.approx(18000.0)
    assert rows["sh600519"]["float_mcap_yi"] <= rows["sh600519"]["mcap_yi"]
    # 腾讯 A：源字段已是万元 150000 → amount 升元
    _assert_amount_pair(rows["sh600519"], expect_wan=150000.0, expect_amount=1_500_000_000.0)


def test_parse_sina_quote_raw_hk_us():
    from scutio_data._providers.quote_parse import parse_sina_quote_raw

    rows = parse_sina_quote_raw(_SINA_HK + _SINA_US)
    assert rows["hk00700"]["price"] == pytest.approx(471.8)
    assert rows["hk00700"]["source"] == "sina"
    _assert_amount_pair(rows["hk00700"], expect_amount=14947725389.176)
    assert rows["usAAPL"]["price"] == pytest.approx(338.19)
    assert rows["usAAPL"]["currency"] == "USD"
    assert rows["usAAPL"]["amount"] is None
    assert rows["usAAPL"]["amount_wan"] is None
    assert rows["usAAPL"]["mcap_yi"] == pytest.approx(49671.1692564)
    assert rows["usAAPL"]["volume"] == 56090797
    assert rows["usAAPL"]["partial"] and not rows["usAAPL"]["coverage"]["amount"]


def test_short_hk_quote_does_not_misread_another_metric_as_pb():
    from scutio_data._providers.quote_parse import parse_tencent_quote_raw

    prefix, body = _TENCENT_HK.split('="', 1)
    fields = body.rstrip(";").strip('"').split("~")
    raw = prefix + '="' + "~".join(fields[:57]) + '";'
    assert parse_tencent_quote_raw(raw)["hk00700"]["pb"] is None


def test_sina_a_quote_preserves_source_date_and_time():
    from scutio_data._providers.quote_parse import parse_sina_quote_raw

    fields = ["0"] * 33
    fields[:6] = ["平安银行", "11.7", "11.8", "11.77", "11.9", "11.6"]
    fields[30:32] = ["2026-09-11", "14:21:27"]
    raw = 'var hq_str_sz000001="' + ",".join(fields) + '";'
    assert parse_sina_quote_raw(raw)["sz000001"]["time"] == "2026-09-11 14:21:27"


def test_sina_class_share_request_and_response_preserve_identity(monkeypatch):
    from scutio_data import market

    def raw(url, **kwargs):
        assert "gb_brk$b" in url
        return _SINA_US.replace("gb_aapl", "gb_brk$b").replace("苹果", "伯克希尔B")

    monkeypatch.setattr(quote_source, "_http_get_bytes", raw)
    env = market.security_quote(["usBRK.B"], sources=["sina"])
    assert env["ok"] and env["missing"] == []
    assert env["quotes"]["usBRK.B"]["code"] == "BRK.B"


def test_sina_us_unchanged_price_keeps_previous_close():
    from scutio_data._providers.quote_parse import parse_sina_quote_raw

    row = parse_sina_quote_raw(_SINA_US.replace("-1.8900", "0.0000"))["usAAPL"]
    assert row["last_close"] == row["price"]


@pytest.mark.parametrize("value", ["", "-", "NaN", "inf", "-100"])
def test_sina_us_missing_market_cap_keeps_valid_price(value):
    from scutio_data._providers.quote_parse import parse_sina_quote_raw

    raw = _SINA_US.replace("4967116925640", value)
    row = parse_sina_quote_raw(raw)["usAAPL"]
    assert row["mcap_yi"] is None
    assert row["price"] == pytest.approx(338.19)
    assert row["amount"] is None


def test_security_quote_sina_us_discloses_missing_turnover(monkeypatch):
    from scutio_data import market

    monkeypatch.setattr(quote_source, "tencent_quote", lambda codes: {})
    monkeypatch.setattr(quote_source, "_http_get_bytes", lambda *a, **k: _SINA_US)
    env = market.security_quote(["usAAPL"], sources=["tencent", "sina"])
    assert env["ok"] and env["partial"]
    row = env["quotes"]["usAAPL"]
    assert row["source"] == "sina" and row["price"] == pytest.approx(338.19)
    assert row["amount"] is None and row["amount_wan"] is None
    assert row["mcap_yi"] == pytest.approx(49671.1692564)
    assert not row["coverage"]["amount"]


def test_security_quote_hk_us_via_tencent(monkeypatch):
    """Drive security_quote entry; inject Tencent bytes (no live network)."""
    from scutio_data import market

    def fake_bytes(url, **kwargs):
        body = ""
        if "hk00700" in url:
            body += _TENCENT_HK
        if "usAAPL" in url or "usaapl" in url.lower():
            body += _TENCENT_US
        return body

    monkeypatch.setattr(quote_source, "_http_get_bytes", fake_bytes)
    # Prevent sina/em fallback attempts if tencent misses
    out = market.security_quote(["hk00700", "usAAPL"])
    assert out["ok"] is True
    assert out["quotes"]["hk00700"]["price"] == pytest.approx(471.8)
    assert out["quotes"]["usAAPL"]["price"] == pytest.approx(338.19)
    assert out["quotes"]["hk00700"]["source"] == "tencent"


def test_security_quote_fallback_sina_when_tencent_empty(monkeypatch):
    from scutio_data import market

    def fake_tc(codes):
        return {}

    def fake_sina(codes):
        return {
            "hk00700": {
                "price": 100.0,
                "last_close": 99.0,
                "symbol": "hk00700",
                "code": "00700",
                "source": "sina",
                "exchange": "hk",
                "currency": "HKD",
            }
        }

    monkeypatch.setattr(quote_source, "tencent_quote", fake_tc)
    monkeypatch.setattr(quote_source, "sina_quote", fake_sina)
    monkeypatch.setattr(
        quote_source,
        "eastmoney_quote",
        lambda codes: (_ for _ in ()).throw(RuntimeError("em down")),
    )
    out = market.security_quote(["hk00700"])
    assert out["ok"] is True
    assert out["quotes"]["hk00700"]["source"] == "sina"
    assert out["quotes"]["hk00700"]["price"] == 100.0


def test_security_quote_a_share_default_uses_tencent(monkeypatch):
    from scutio_data import market

    def fake_tc(codes):
        return {
            "sh600519": {
                "price": 1500.0,
                "last_close": 1490.0,
                "symbol": "sh600519",
                "code": "600519",
                "source": "tencent",
                "exchange": "sh",
                "currency": "CNY",
            }
        }

    monkeypatch.setattr(quote_source, "tencent_quote", fake_tc)
    out = market.security_quote(["600519"])
    assert out["ok"] is True
    assert out["quotes"]["sh600519"]["price"] == 1500.0


def test_hk_bars_use_akshare_and_preserve_share_volume(monkeypatch):
    from scutio_data import market
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(function, **params):
        assert function == "stock_hk_hist" and params["symbol"] == "00700"
        assert params["adjust"] == ""
        return [
            {
                "日期": "2026-09-08T00:00:00",
                "开盘": 470,
                "收盘": 471.8,
                "最高": 475,
                "最低": 460,
                "成交量": 17643957,
                "成交额": 8000000000,
            }
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = market.security_bars("hk00700", count=1)
    assert out["ok"] and out["source"] == "akshare_eastmoney"
    assert (out["symbol"], out["code"], out["currency"]) == ("hk00700", "00700", "HKD")
    assert out["bars"][0]["datetime"] == "2026-09-08"
    assert out["bars"][0]["volume"] == 17643957
    assert out["bars"][0]["amount_unit"] == "HKD"


def test_hk_weekly_bars_ask_akshare_for_weekly_data(monkeypatch):
    from scutio_data import market
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(function, **params):
        assert function == "stock_hk_hist" and params["period"] == "weekly"
        return [
            {
                "日期": "2026-09-04",
                "开盘": 465,
                "收盘": 456.4,
                "最高": 479.6,
                "最低": 454,
                "成交量": 152689790,
            }
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = market.security_bars("hk00700", frequency="W", count=1)
    assert out["ok"] and out["frequency"] == "W"
    assert out["bars"][0]["close"] == 456.4
    assert out["bars"][0]["amount"] is None


def test_security_bars_us_prefers_eastmoney(monkeypatch):
    from scutio_data import market
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(function, **params):
        assert function == "stock_us_hist" and params["symbol"] == "105.AAPL"
        assert params["adjust"] == ""
        return [
            {
                "日期": "2026-09-08",
                "开盘": 340,
                "收盘": 338.19,
                "最高": 344.57,
                "最低": 337.35,
                "成交量": 56090840,
            }
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = market.security_bars("usAAPL", count=1)
    assert out["ok"] and out["source"] == "akshare_eastmoney"
    assert (out["symbol"], out["code"], out["currency"]) == ("usAAPL", "AAPL", "USD")
    assert out["bars"][0]["volume"] == 56090840
    assert out["bars"][0]["close"] == pytest.approx(338.19)


def test_security_bars_rejects_sparse_series_with_multi_year_gap(monkeypatch):
    from scutio_data import market

    def bars(*args, source, **kwargs):
        if source == "eastmoney":
            raise RuntimeError("em down")
        return [
            {"datetime": "2011-06-02", "open": 346, "close": 346, "high": 347, "low": 345},
            {"datetime": "2026-08-07", "open": 311, "close": 313, "high": 315, "low": 310},
        ]

    monkeypatch.setattr(akshare_market, "bars", bars)
    out = market.security_bars("usAAPL", count=60)
    assert not out["ok"] and out["bars"] == []
    assert "sparse_excessive_gap_days" in out["errors"]["akshare_sina"]


def test_akshare_us_bars_resolves_exchange_and_caches_match(monkeypatch):
    from scutio_data import market
    from scutio_data._providers.akshare import client as akshare_source
    from scutio_data._providers.eastmoney import em_secid

    seen = []

    def fetch(function, **params):
        seen.append(params["symbol"])
        if params["symbol"] == "106.PATH":
            return [{"日期": "2026-09-08", "开盘": 10, "收盘": 11, "最高": 12, "最低": 9}]
        return []

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = market.security_bars("usPATH", count=1)
    assert out["ok"] and seen == ["105.PATH", "106.PATH"]
    assert em_secid("usPATH") == "106.PATH"


def test_security_bars_keeps_short_contiguous_history_as_partial(monkeypatch):
    from scutio_data import market

    monkeypatch.setattr(
        akshare_market,
        "bars",
        lambda *a, **k: [
            {"datetime": "2026-08-06", "open": 10, "close": 10.5, "high": 11, "low": 9.5},
            {"datetime": "2026-08-07", "open": 10.5, "close": 11, "high": 11.2, "low": 10.2},
        ],
    )
    out = market.security_bars("usNEW", count=60, sources=("akshare_sina",))
    assert out["ok"] is True
    assert out["partial"] is True
    assert out["quality"]["status"] == "partial"


def test_security_bars_marks_five_of_sixty_rows_partial(monkeypatch):
    from scutio_data import market

    monkeypatch.setattr(
        akshare_market,
        "bars",
        lambda *a, **k: [
            {
                "datetime": f"2026-08-0{day}",
                "open": 10,
                "close": 10,
                "high": 11,
                "low": 9,
            }
            for day in range(3, 8)
        ],
    )
    out = market.security_bars("usNEW", count=60, sources=("akshare_sina",))
    assert out["ok"] is True
    assert out["partial"] is True
    assert out["quality"]["status"] == "partial"
    assert out["quality"]["returned_count"] == 5


def test_eastmoney_us_route_probes_market_id_and_caches_match(monkeypatch):
    from scutio_data._providers.eastmoney import em_secid

    seen = []

    class FakeResp:
        def __init__(self, secid):
            self.secid = secid

        def json(self):
            if self.secid == "106.PATH":
                return {
                    "data": {
                        "f57": "PATH",
                        "f58": "UiPath",
                        "f43": 12.0,
                        "f60": 11.5,
                        "f48": 1000,
                    }
                }
            return {"data": None}

    def fake_em_get(_url, params=None, **_kwargs):
        seen.append(params["secid"])
        return FakeResp(params["secid"])

    monkeypatch.setattr(providers_eastmoney, "em_get", fake_em_get)
    out = quote_source.eastmoney_quote(["usPATH"])
    assert out["usPATH"]["price"] == 12.0
    assert seen == ["105.PATH", "106.PATH"]
    assert em_secid("usPATH") == "106.PATH"


def test_akshare_tencent_corrects_shenzhen_equity_volume(monkeypatch):
    from scutio_data import market
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(function, **params):
        assert function == "stock_zh_a_hist_tx" and params["symbol"] == "sz000725"
        assert "-" in params["end_date"]
        return [
            {
                "date": "2026-09-08",
                "open": 4,
                "close": 4,
                "high": 5,
                "low": 3,
                "volume": 1000,
                "amount": 400000,
            }
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = market.security_bars("000725", sources=("akshare_tencent",), count=1)
    assert out["ok"] and out["bars"][0]["volume"] == 100000


@pytest.mark.parametrize(
    ("code", "upstream_volume", "expected_shares"),
    [
        ("sh000001", 484675114, 48467511400),
        ("sz399001", 559023425, 55902342500),
        ("bj899050", 790599300, 790599300),
        ("sh688981", 100000, 100000),
    ],
)
def test_akshare_tencent_index_volume_units(monkeypatch, code, upstream_volume, expected_shares):
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **kw: [
            {
                "date": "2026-09-10",
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": upstream_volume,
            }
        ],
    )
    bar = akshare_market.bars(code, source="tencent", count=1)[0]
    assert bar["volume"] == expected_shares and bar["volume_unit"] == "share"


def test_sina_etf_uses_fund_history_and_rejects_unverified_adjustment(monkeypatch):
    from scutio_data._providers.akshare import client as akshare_source

    calls = []

    def fetch(function, **params):
        calls.append((function, params))
        return [
            {
                "date": "2026-09-10",
                "open": 4.617,
                "high": 4.64,
                "low": 4.599,
                "close": 4.617,
                "volume": 438854072,
                "amount": 2026289592,
            }
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    bar = akshare_market.bars("510300", source="sina", count=1)[0]
    assert calls == [("fund_etf_hist_sina", {"symbol": "sh510300"})]
    assert bar["volume"] == 438854072 and bar["amount"] == 2026289592
    with pytest.raises(ValueError, match="unadjusted"):
        akshare_market.bars("510300", source="sina", adjust="qfq", count=1)
    assert len(calls) == 1


def test_tencent_index_missing_pb_and_price_limits_are_null():
    from scutio_data._providers.quote_parse import parse_tencent_quote_raw

    parts = _TENCENT_A.split('"')[1].split("~")
    parts[2] = "000001"
    parts[46:49] = ["0", "-1", "-1"]
    row = parse_tencent_quote_raw('v_sh000001="' + "~".join(parts) + '";')["sh000001"]
    assert row["pb"] is row["limit_up"] is row["limit_down"] is None


def test_eastmoney_stock_info_hk_us_secid(monkeypatch):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source, "fetch", lambda fn, **kw: [{"公司名称": "腾讯控股", "所属行业": "互联网"}]
    )
    monkeypatch.setattr(
        quote_source, "tencent_quote", lambda codes: {"usAAPL": {"name": "Apple", "price": 338.19}}
    )
    hk = fundamentals.stock_info("hk00700")
    us = fundamentals.stock_info("usAAPL")
    assert hk["source"] == "akshare_hk_profile" and hk["industry"] == "互联网"
    assert hk["total_shares"] is None and hk["partial"]
    assert us["code"] == "AAPL" and us["partial"] and us["price"] == 338.19


def test_stock_info_hk_tencent_fallback_when_em_empty(monkeypatch):
    from scutio_data import fundamentals

    def fake_em_get(url, params=None, **kwargs):
        return type("R", (), {"json": lambda self: {"data": {}}})()

    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(
        quote_source,
        "tencent_quote",
        lambda codes: {
            "hk00700": {
                "name": "腾讯控股",
                "price": 471.8,
                "mcap_yi": 42898.0,
                "float_mcap_yi": 42898.0,
            }
        },
    )
    # re-import path: fundamentals imports market inside fallback
    info = fundamentals.stock_info("hk00700")
    assert info["name"] == "腾讯控股"
    assert info["source"] == "tencent_quote_fallback"
    assert info["price"] == pytest.approx(471.8)
    assert info["partial"] is True
    assert info["data_quality"] == "partial_fallback"


def test_mixed_batch_quote_market_aware_chains(monkeypatch):
    """A + HK in one batch: A uses tencent, never forces HK through Sina."""
    from scutio_data import market

    def fake_tc(codes):
        out = {}
        for c in codes:
            s = str(c)
            if "600519" in s or s.endswith("600519"):
                out["sh600519"] = {
                    "price": 1.0,
                    "last_close": 1.0,
                    "symbol": "sh600519",
                    "code": "600519",
                    "source": "tencent",
                    "exchange": "sh",
                }
            if "00700" in s or "hk" in s.lower():
                out["hk00700"] = {
                    "price": 2.0,
                    "last_close": 2.0,
                    "symbol": "hk00700",
                    "code": "00700",
                    "source": "tencent",
                    "exchange": "hk",
                }
        return out

    monkeypatch.setattr(quote_source, "tencent_quote", fake_tc)
    out = market.security_quote(["600519", "hk00700"])
    assert out["ok"] is True
    assert out["quotes"]["sh600519"]["price"] == 1.0
    assert out["quotes"]["hk00700"]["price"] == 2.0
