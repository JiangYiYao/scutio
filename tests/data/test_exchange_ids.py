"""Exchange id helpers aligned with split_code (offline)."""

from __future__ import annotations

import pytest
from scutio_data._providers.akshare import market as akshare_market


def test_em_secid_uses_split_code_not_startswith_6_only():
    from scutio_data._providers.eastmoney import em_secid, em_src_security_code

    # Shanghai equities and 5xxxxx (funds/etc. heuristic → sh)
    assert em_secid("600519") == "1.600519"
    assert em_secid("sh600519") == "1.600519"
    assert em_secid("600519.SH") == "1.600519"
    assert em_secid("510050") == "1.510050"  # old startswith("6") wrongly used 0.

    # Shenzhen
    assert em_secid("000001") == "0.000001"
    assert em_secid("sz000001") == "0.000001"

    # Beijing-style
    assert em_secid("899050") == "0.899050"
    assert em_secid("bj899050") == "0.899050"

    assert em_src_security_code("510050") == "SH510050"
    assert em_src_security_code("000001") == "SZ000001"
    assert em_src_security_code("899050") == "BJ899050"


def test_split_code_hk_and_us_explicit():
    from scutio_data._providers.eastmoney import em_secid, source_symbol
    from scutio_data._runtime.symbols import canonical_symbol, market_of, split_code

    assert split_code("hk00700") == ("hk", "00700")
    assert split_code("00700.HK") == ("hk", "00700")
    assert split_code("HK00700") == ("hk", "00700")
    assert split_code("hk700") == ("hk", "00700")

    assert split_code("usAAPL") == ("us", "AAPL")
    assert split_code("USAAPL") == ("us", "AAPL")
    assert split_code("AAPL.US") == ("us", "AAPL")
    assert split_code("US.AAPL") == ("us", "AAPL")
    assert split_code("usBRK.B") == ("us", "BRK.B")
    assert split_code("BRK.B.US") == ("us", "BRK.B")

    assert em_secid("hk00700") == "116.00700"
    assert em_secid("usAAPL") == "105.AAPL"
    assert em_secid("AAPL.US") == "105.AAPL"

    assert canonical_symbol("00700.HK") == "hk00700"
    assert canonical_symbol("AAPL.US") == "usAAPL"
    assert market_of("usAAPL") == "us"
    assert market_of("hk00700") == "hk"
    assert market_of("600519") == "a"

    assert source_symbol("hk00700", "tencent") == "hk00700"
    assert source_symbol("usAAPL", "tencent") == "usAAPL"
    assert source_symbol("hk00700", "sina") == "rt_hk00700"
    assert source_symbol("usAAPL", "sina") == "gb_aapl"
    assert source_symbol("usAAPL", "eastmoney_secid") == "105.AAPL"
    assert source_symbol("hk00700", "em_secid") == "116.00700"


def test_bare_us_ticker_rejected():
    from scutio_data._providers.eastmoney import em_secid
    from scutio_data._runtime.symbols import split_code

    with pytest.raises(ValueError, match="explicit"):
        split_code("AAPL")
    with pytest.raises(ValueError, match="explicit"):
        split_code("TSLA")
    with pytest.raises(ValueError):
        em_secid("AAPL")


def test_company_name_is_rejected_before_news_provider_without_us_ticker_hint(monkeypatch):
    from scutio_data import feeds
    from scutio_data._providers.akshare import client
    from scutio_data._runtime.symbols import split_code

    with pytest.raises(ValueError, match="security code, not a company name") as error:
        split_code("贵州茅台")
    assert "US ticker" not in str(error.value)
    calls = []
    monkeypatch.setattr(client, "fetch", lambda *a, **kw: calls.append(True))
    result = feeds.stock_news("贵州茅台")
    assert result["ok"] is False
    assert "security code, not a company name" in result["error"]
    assert calls == []


@pytest.mark.parametrize(
    "raw",
    ["600519foo", "1234567", "hk123456", "600519.HK", "sz1234567"],
)
def test_split_code_rejects_inputs_that_could_mutate_identity(raw):
    from scutio_data._runtime.symbols import split_code

    with pytest.raises(ValueError):
        split_code(raw)


def test_a_only_helpers_reject_hk_us():
    from scutio_data._providers.eastmoney import (
        em_src_security_code,
    )

    for helper in (em_src_security_code,):
        with pytest.raises(ValueError):
            helper("hk00700")
        with pytest.raises(ValueError):
            helper("usAAPL")


def test_stock_info_rejects_fund_before_company_provider(monkeypatch):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    calls = []

    def fetch(fn, **kw):
        calls.append((fn, kw))
        return [{"item": "股票代码", "value": "510050"}, {"item": "股票简称", "value": "50ETF"}]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = fundamentals.stock_info("510050")
    assert not out["ok"] and out["error_code"] == "unsupported_asset"
    assert calls == []


def test_financial_report_preserves_exchange_in_akshare_symbol(monkeypatch):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    calls = []
    monkeypatch.setattr(akshare_source, "fetch", lambda fn, **kw: calls.append((fn, kw)) or [])
    assert not fundamentals.financial_report("510050")["ok"]
    fundamentals.financial_report("sh600519")
    assert [kw["symbol"] for fn, kw in calls] == ["SH600519"]


def test_materials_reject_hk_us_report_accepts_explicit_codes(monkeypatch):
    from scutio_data import fundamentals

    us_materials = fundamentals.stock_materials("usAAPL")
    hk_materials = fundamentals.stock_materials("hk00700")
    assert us_materials["ok"] is False
    assert hk_materials["ok"] is False
    assert us_materials["error_code"] == "unsupported_market"
    assert hk_materials["error_code"] == "unsupported_market"
    assert not hasattr(fundamentals, "finance_snapshot")
    assert not hasattr(fundamentals, "stock_profile")

    monkeypatch.setattr(akshare_market, "profile", lambda code: {"机构简介": "公司正文"})
    cats = fundamentals.stock_materials("600519")
    body = fundamentals.stock_materials("600519", "公司概况")
    assert cats["ok"] is True and cats["items"][0]["name"] == "公司概况"
    assert body["ok"] is True and body["text"] == "公司正文"

    # financial_report 支持港美：这里只验证会走到港/美分支而非 A-share only
    monkeypatch.setattr(
        fundamentals,
        "_financial_report_hk",
        lambda pure, report_type="lrb", num=8, period="annual": [
            {"报告期": "2024-12-31", "营业额": 1, "名称": "mock"}
        ],
    )
    monkeypatch.setattr(
        fundamentals,
        "_financial_report_us",
        lambda pure, report_type="lrb", num=8, period="annual": [
            {"报告期": "2024-12-31", "营业收入": 2, "名称": "mock"}
        ],
    )
    hk = fundamentals.financial_report("hk00700", "lrb", 2)
    us = fundamentals.financial_report("usAAPL", "lrb", 2)
    assert hk["ok"] is True and hk["items"][0]["报告期"] == "2024-12-31"
    assert us["ok"] is True and us["items"][0]["营业收入"] == 2
