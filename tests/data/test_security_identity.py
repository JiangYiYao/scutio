"""Company boundaries and provider identities must survive shared numeric codes."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from scutio_data import (
    announcements,
    capital,
    events,
    feeds,
    fundamentals,
    market,
    research,
    valuation,
)
from scutio_data._providers import cninfo, eastmoney, quotes
from scutio_data._providers.akshare import client, snapshots
from scutio_data._providers.akshare import market as akshare_market
from scutio_data._runtime.symbols import (
    require_a_share,
    require_security,
    security_kind,
    validate_report_identity,
)

COMPANY_ENTRIES = [
    (fundamentals.stock_info, {}),
    (fundamentals.financial_report, {}),
    (fundamentals.stock_materials, {"name": "主营业务"}),
    (valuation.valuation_history, {}),
    (announcements.stock_announcements, {}),
    (announcements.periodic_reports, {}),
    (announcements.irm, {}),
    (announcements.lockup_expiry, {"trade_date": "2026-09-11"}),
    (capital.margin_trading, {}),
    (capital.block_trade, {}),
    (capital.holder_num_change, {}),
    (capital.dividend_history, {}),
    (capital.share_repurchases, {}),
    (capital.shareholder_changes, {}),
    (capital.pledge_status, {}),
    (capital.corporate_actions, {}),
    (capital.dragon_tiger_board, {"trade_date": "2026-09-11"}),
    (capital.concept_blocks, {}),
    (capital.stock_fund_flow_120d, {}),
    (research.stock_reports, {}),
    (research.eps_forecast, {}),
    (research.consensus_forecast, {}),
    (research.consensus_revisions, {}),
    (research.list_local_reports, {}),
    (feeds.stock_news, {}),
    (events.suspensions, {}),
    (events.earnings_calendar, {"report_date": "2026-06-30"}),
    (events.performance_updates, {"report_date": "2026-06-30"}),
    (events.company_events, {}),
]


@pytest.mark.parametrize(
    "entry,params", COMPANY_ENTRIES, ids=[fn.__name__ for fn, _ in COMPANY_ENTRIES]
)
@pytest.mark.parametrize(
    "code",
    [
        "sh000001",
        "000001.SH",
        "sz600519",
        "bj000001",
        "sh300750",
        "sh510300",
        "sz159915",
        "sz399001",
        "bj899050",
    ],
)
def test_company_entries_reject_other_assets_before_provider(monkeypatch, entry, params, code):
    network = Mock(side_effect=AssertionError("invalid identity reached a provider"))
    monkeypatch.setattr(client, "fetch", network)
    monkeypatch.setattr(snapshots, "fetch_snapshot", network)
    monkeypatch.setattr(eastmoney, "em_get", network)
    monkeypatch.setattr(quotes, "tencent_quote", network)
    out = entry(code=code, **params)
    assert out["ok"] is False
    assert out["error"].startswith("unsupported_asset:")
    network.assert_not_called()


@pytest.mark.parametrize(
    "code,symbol",
    [
        ("000001", "sz000001"),
        ("000001.SZ", "sz000001"),
        ("sh600519", "sh600519"),
        ("sh688981", "sh688981"),
        ("sz300750", "sz300750"),
        ("bj920000", "bj920000"),
        ("bj430047", "bj430047"),
    ],
)
def test_valid_company_codes_keep_exchange_in_company_requests(monkeypatch, code, symbol):
    market_key, prefix, pure = require_a_share(code, "test")
    assert market_key == "a" and prefix + pure == symbol
    fetch = Mock(return_value=[])
    monkeypatch.setattr(client, "fetch", fetch)
    result = fundamentals.financial_report(code)
    assert result["ok"]
    fetch.assert_called_once_with("stock_profit_sheet_by_report_em", symbol=symbol.upper())


@pytest.mark.parametrize(
    "code,function,params",
    [
        (
            "hk00700",
            "stock_financial_hk_report_em",
            {"stock": "00700", "symbol": "利润表", "indicator": "年度"},
        ),
        (
            "usBRK.B",
            "stock_financial_us_report_em",
            {"stock": "BRK_B", "symbol": "综合损益表", "indicator": "年报"},
        ),
    ],
)
def test_overseas_financial_routes_preserve_explicit_identity(monkeypatch, code, function, params):
    fetch = Mock(return_value=[])
    monkeypatch.setattr(client, "fetch", fetch)
    assert fundamentals.financial_report(code)["ok"]
    fetch.assert_called_once_with(function, **params)


@pytest.mark.parametrize(
    "row",
    [
        {
            "symbol": "sh000001",
            "code": "000001",
            "exchange": "sh",
            "name": "上证指数",
            "price": 3000,
        },
        {"symbol": "sz000001", "code": "000001", "exchange": "sh", "name": "冲突身份", "price": 11},
        {"symbol": "sz000002", "code": "000002", "name": "另一家公司", "price": 11},
    ],
)
def test_stock_info_rejects_conflicting_fallback_identity(monkeypatch, row):
    monkeypatch.setattr(client, "fetch", Mock(side_effect=RuntimeError("primary unavailable")))
    monkeypatch.setattr(quotes, "tencent_quote", Mock(return_value={"sz000001": row}))
    out = fundamentals.stock_info("sz000001")
    assert not out["ok"]
    assert out["name"] == "" and out["price"] is None


@pytest.mark.parametrize(
    "code,symbol,pure",
    [
        ("sz000001", "sz000001", "000001"),
        ("hk00700", "hk00700", "00700"),
        ("usBRK.B", "usBRK.B", "BRK.B"),
    ],
)
def test_stock_info_fallback_keeps_verified_security(monkeypatch, code, symbol, pure):
    monkeypatch.setattr(client, "fetch", Mock(side_effect=RuntimeError("primary unavailable")))
    fetch = Mock(
        return_value={
            symbol: {"symbol": symbol, "code": pure, "name": "Requested issuer", "price": 11}
        }
    )
    monkeypatch.setattr(quotes, "tencent_quote", fetch)
    out = fundamentals.stock_info(code)
    assert out["ok"] and out["partial"] and out["symbol"] == symbol and out["code"] == pure
    fetch.assert_called_once_with([symbol])


def test_financial_report_rejects_conflicting_native_exchange(monkeypatch):
    monkeypatch.setattr(
        client,
        "fetch",
        Mock(
            return_value=[
                {"SECURITY_CODE": "000001", "SECUCODE": "000001.SH", "REPORT_DATE": "2025-12-31"}
            ]
        ),
    )
    out = fundamentals.financial_report("sz000001")
    assert not out["ok"] and "identity mismatch" in out["error"]


@pytest.mark.parametrize("provider", [akshare_market.profile, cninfo.announcement_rows])
def test_company_provider_also_guards_full_identity(monkeypatch, provider):
    fetch = Mock()
    monkeypatch.setattr(client, "fetch", fetch)
    with pytest.raises(ValueError, match="unsupported_asset"):
        provider("sh000001")
    fetch.assert_not_called()


@pytest.mark.parametrize(
    "code,kind,function",
    [
        ("sh000001", "index", "index_zh_a_hist"),
        ("sz399001", "index", "index_zh_a_hist"),
        ("bj899050", "index", "index_zh_a_hist"),
        ("sh510300", "fund", "fund_etf_hist_em"),
        ("sz159915", "fund", "fund_etf_hist_em"),
        ("sz000001", "company", "stock_zh_a_hist"),
    ],
)
def test_bar_asset_routes_keep_index_fund_company_separate(monkeypatch, code, kind, function):
    assert security_kind(code) == kind
    fetch = Mock(return_value=[])
    monkeypatch.setattr(client, "fetch", fetch)
    assert akshare_market.bars(code, source="eastmoney") == []
    assert fetch.call_args.args[0] == function
    assert fetch.call_args.kwargs["symbol"] == code[2:]


@pytest.mark.parametrize("code", ["sz600519", "sh300750", "bj000001", "sz510300"])
def test_invalid_exchange_rejected_by_market_provider(monkeypatch, code):
    fetch = Mock()
    monkeypatch.setattr(client, "fetch", fetch)
    with pytest.raises(ValueError, match="unsupported_asset"):
        require_security(code)
    with pytest.raises(ValueError, match="unsupported_asset"):
        market.security_bars(code)
    fetch.assert_not_called()


@pytest.mark.parametrize(
    "envelope",
    [
        {"ok": True, "symbol": "sh000001", "items": [{"stockCode": "000001"}]},
        {
            "ok": True,
            "symbol": "sz000001",
            "items": [{"symbol": "sh000001", "stockCode": "000001"}],
        },
        {
            "ok": True,
            "symbol": "sz000001",
            "items": [{"symbol": "sz000001", "stockCode": "000001", "exchange": "sh"}],
        },
        {"ok": True, "items": []},
    ],
)
def test_reused_report_identity_checks_envelope_rows_and_exchange(envelope):
    with pytest.raises(ValueError, match="identity"):
        validate_report_identity(envelope, "sz000001")
    assert not research.consensus_revisions("sz000001", reports=envelope)["ok"]


@pytest.mark.parametrize(
    "code,wrong_code", [("sz000001", "000002"), ("hk00700", "00941"), ("usBRK.B", "BRKB")]
)
def test_eastmoney_quote_rejects_a_different_security(monkeypatch, code, wrong_code):
    response = Mock()
    response.json.return_value = {
        "data": {"f57": wrong_code, "f58": "Wrong issuer", "f43": 10, "f60": 9}
    }
    monkeypatch.setattr(eastmoney, "em_get", Mock(return_value=response))
    result = market.security_quote([code], sources=["eastmoney"])
    assert not result["ok"] and result["quotes"] == {}
    assert result["missing"] == [code]


def _tencent_wire(symbol, payload_code):
    lines = (
        (Path(__file__).parents[1] / "fixtures/data_audit/tencent_quotes.txt")
        .read_text()
        .splitlines()
    )
    source_symbol = (
        "usAAPL"
        if symbol.startswith("us")
        else "hk00700"
        if symbol.startswith("hk")
        else "sh600519"
    )
    wire = next(line for line in lines if line.startswith("v_" + source_symbol + "="))
    fields = wire.split('"')[1].split("~")
    fields[2] = payload_code
    return "v_" + symbol + '="' + "~".join(fields) + '";'


@pytest.mark.parametrize(
    "symbol,payload_code",
    [
        ("sz000001", "000002"),
        ("hk00700", "00941"),
        ("usAAPL", "MSFT.OQ"),
        ("usAAPL", "AAPL.UNKNOWN"),
        ("usBRK.B", "BRK.A.N"),
        ("usBRK.B", "BRKB.N"),
        ("sz000001", ""),
    ],
)
def test_tencent_payload_code_conflict_rejected_through_public_quote(
    monkeypatch, symbol, payload_code
):
    monkeypatch.setattr(
        quotes, "_http_get_bytes", Mock(return_value=_tencent_wire(symbol, payload_code))
    )
    result = market.security_quote([symbol], sources=["tencent"])
    assert not result["ok"] and result["quotes"] == {}
    assert result["missing"] == [symbol]


@pytest.mark.parametrize(
    "symbol,payload_code",
    [
        ("sz000001", "000001"),
        ("sh000001", "000001"),
        ("sh510300", "510300"),
        ("sz159915", "159915"),
        ("hk00700", "00700"),
        ("usAAPL", "AAPL.OQ"),
        ("usBRK.A", "BRK.A.N"),
        ("usBRK.B", "BRK.B.N"),
        ("usBRK.B", "BRK.B"),
    ],
)
def test_tencent_verified_payload_keeps_index_fund_and_class_shares(
    monkeypatch, symbol, payload_code
):
    monkeypatch.setattr(
        quotes, "_http_get_bytes", Mock(return_value=_tencent_wire(symbol, payload_code))
    )
    result = market.security_quote([symbol], sources=["tencent"])
    assert result["ok"] and result["missing"] == []
    assert result["quotes"][symbol]["symbol"] == symbol


def test_tencent_conflicting_row_falls_back_without_losing_valid_batch_rows(monkeypatch):
    def get(url, **kwargs):
        if "qt.gtimg.cn" in url:
            return _tencent_wire("sz000001", "000002") + _tencent_wire("sz000002", "000002")
        assert url.endswith("list=sz000001")
        return 'var hq_str_sz000001="平安银行,10,10,11,11,10,0,0,100,1100";'

    monkeypatch.setattr(quotes, "_http_get_bytes", get)
    result = market.security_quote(["sz000001", "sz000002"], sources=["tencent", "sina"])
    assert result["ok"] and result["missing"] == []
    assert result["quotes"]["sz000001"]["source"] == "sina"
    assert result["quotes"]["sz000001"]["price"] == 11
    assert result["quotes"]["sz000002"]["source"] == "tencent"


@pytest.mark.parametrize(
    "symbol,key,payload",
    [
        ("sz000001", "sz000001", "平安银行,10,10,11,11,10,0,0,100,1100"),
        ("hk00700", "rt_hk00700", "TENCENT,腾讯控股,10,10,11,10,11,1,10,10,11,1100,100"),
        ("usAAPL", "gb_aapl", "苹果,11,10,2026-09-11,1,10,11,10,12,8,100,90,10000,1,10"),
        ("usBRK.B", "gb_brk$b", "伯克希尔B,11,10,2026-09-11,1,10,11,10,12,8,100,90,10000,1,10"),
    ],
)
@pytest.mark.parametrize("wrong_key", [False, True])
def test_sina_variable_identity_is_preserved_without_name_inference(
    monkeypatch, symbol, key, payload, wrong_key
):
    if wrong_key:
        key = {
            "sz000001": "sh000001",
            "rt_hk00700": "rt_hk00941",
            "gb_aapl": "gb_msft",
            "gb_brk$b": "gb_brk$a",
        }[key]
    wire = "var hq_str_" + key + '="' + payload + '";'
    monkeypatch.setattr(quotes, "_http_get_bytes", Mock(return_value=wire))
    result = market.security_quote([symbol], sources=["sina"])
    assert result["ok"] is not wrong_key
    if wrong_key:
        assert result["quotes"] == {} and result["missing"] == [symbol]
    else:
        assert result["quotes"][symbol]["symbol"] == symbol
