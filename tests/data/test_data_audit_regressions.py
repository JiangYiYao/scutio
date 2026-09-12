"""Public source samples from the 2026-09-09 five-stock audit; no live network."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import scutio_data._providers.eastmoney as providers_eastmoney
import scutio_data.capital.dividends as capital_dividends
from scutio_data import capital, fundamentals
from scutio_data._providers import quotes as quote_source
from scutio_data._providers.quote_parse import parse_tencent_quote_raw, quote_volume

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "data_audit"


def response(payload):
    return SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None)


@pytest.mark.parametrize("num", [2, 8])
def test_annual_request_fetches_years_before_applying_limit(monkeypatch, num):
    from scutio_data._providers.akshare import client as akshare_source

    rows = [
        {"SECURITY_CODE": "600519", "REPORT_DATE": day}
        for day in ["2026-06-30", "2026-03-31"]
        + [f"{year}-12-31" for year in range(2025, 2017, -1)]
    ]
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: rows)
    annual = fundamentals.financial_report("600519", num=num)
    assert annual["ok"] and not annual["partial"] and len(annual["items"]) == num
    assert annual["items"][-1]["报告期"] == f"{2026 - num}-12-31"
    latest = fundamentals.financial_report("600519", num=2, period="all")
    assert [row["报告期"] for row in latest["items"]] == ["2026-06-30", "2026-03-31"]


def test_report_shortfall_is_partial_and_error_body_is_failure(monkeypatch):
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [])
    env = fundamentals.financial_report("600519", num=2)
    assert env["ok"] and env["partial"] and env["returned_count"] == 0

    def failure(*a, **k):
        raise ValueError("missing source data")

    monkeypatch.setattr(akshare_source, "fetch", failure)
    assert not fundamentals.financial_report("600519")["ok"]


def test_income_native_fields_preserve_distinct_interest_accounts(monkeypatch):
    from scutio_data._providers.akshare import client as akshare_source

    raw = {
        "SECURITY_CODE": "600519",
        "REPORT_DATE": "2026-06-30",
        "INTEREST_INCOME": 1574811118.73,
        "FE_INTEREST_INCOME": 266574096.14,
        "INTEREST_INCOME_YOY": -7.604,
        "EMPTY_ACCOUNT": None,
    }
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [raw])
    row = fundamentals.financial_report("600519", period="all")["items"][0]
    assert row["INTEREST_INCOME"] == 1574811118.73 and row["FE_INTEREST_INCOME"] == 266574096.14
    assert row["EMPTY_ACCOUNT"] is None
    fields = {x["field_id"]: x for x in row["_line_items"]}
    assert (
        fields["INTEREST_INCOME"]["yoy"] == -7.604
        and fields["INTEREST_INCOME"]["yoy_unit"] == "pct"
    )
    assert len(fields) == len(row["_line_items"])


@pytest.mark.parametrize(
    "symbol,expected,precision",
    [
        ("sh600519", 3222600, 100),
        ("sz300750", 39024000, 100),
        ("sh601398", 215443200, 100),
        ("sh688981", 21857641, 1),
        ("sz000725", 737152500, 100),
        ("sh510300", 573288700, 100),
        ("bj920002", 647300, 100),
        ("hk00700", 17643957, 1),
        ("usAAPL", 35477090, 1),
    ],
)
def test_quote_volume_units_across_boards_and_markets(symbol, expected, precision):
    row = parse_tencent_quote_raw((FIXTURES / "tencent_quotes.txt").read_text(encoding="utf-8"))[
        symbol
    ]
    assert row["volume"] == expected
    assert row["volume_unit"] == "share"
    assert row["volume_precision"] == precision
    assert row["volume"] == row["volume_raw"] * precision
    assert row["pe_static"] is None
    if symbol == "sh600519":
        assert row["pe_dynamic"] == 18.12
    if symbol.startswith(("hk", "us")):
        assert row["pe_dynamic"] is None


@pytest.mark.parametrize("raw", [None, "", "-", "NaN", "inf", -1])
def test_missing_or_invalid_volume_is_not_zero(raw):
    assert quote_volume(raw, "lot")["volume"] is None
    assert quote_volume(0, "lot")["volume"] == 0


def test_eastmoney_star_quote_is_lots_unlike_tencent(monkeypatch):
    monkeypatch.setattr(
        providers_eastmoney,
        "em_get",
        lambda *a, **k: response({"data": {"f57": "688981", "f47": 218576, "f43": 100}}),
    )
    row = quote_source.eastmoney_quote(["688981"])["sh688981"]
    assert row["volume"] == 21857600
    assert row["volume_precision"] == 100


def test_special_dividends_replace_daily_summary_without_double_counting(monkeypatch):
    from scutio_data._providers.akshare import client as akshare_source

    sample = json.loads((FIXTURES / "catl_dividends_cninfo.json").read_text(encoding="utf-8"))

    def fetch(function, **params):
        assert function == "stock_dividend_cninfo" and params["symbol"] == "300750"
        return sample + [sample[5]]  # repeated upstream event is not another payout

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    env = capital.dividend_history("300750", page_size=20)
    rows = {row["date"]: row for row in env["items"]}
    assert rows["2024-04-30"]["bonus_rmb"] == 50.28
    assert rows["2024-04-30"]["dividend_per_share"] == 5.028
    assert len(rows["2024-04-30"]["components"]) == 2
    assert rows["2025-01-24"]["bonus_rmb"] == 12.3
    assert rows["2026-04-22"]["bonus_rmb"] == 69.57
    assert len(rows) == len(env["items"]) == 11
    assert not env["partial"] and env["special_dividend_coverage"]["available"]
    assert rows["2019-07-22"]["bonus_rmb"] == 1.42037
    assert len(capital.dividend_history("300750", page_size=5)["items"]) == 5


def test_event_failure_keeps_regular_dividends_with_partial_warning(monkeypatch):
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(function, **params):
        if function == "stock_dividend_cninfo":
            raise RuntimeError("source unavailable")
        return [{"除权除息日": "2024-04-30", "现金分红-现金分红比例": None}]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    env = capital.dividend_history("300750")
    assert env["ok"] and env["partial"] and env["warning"]
    assert env["items"][0]["dividend_per_share"] is None
    assert not env["special_dividend_coverage"]["available"]
    monkeypatch.setattr(
        akshare_source, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    assert capital.dividend_history("300750")["ok"] is False


def test_event_parser_preserves_stock_distribution_and_rejects_partial_day():
    event = {
        "ASSIGN_PROGRESS": "实施方案",
        "EX_DIVIDEND_DATE": "2024-01-01",
        "IMPL_PLAN_PROFILE": "10送2转增8派25.2元",
    }
    rows, _, errors = capital_dividends._dividend_event_rows([event])
    assert not errors
    assert rows["2024-01-01"]["bonus_ratio"] == 2
    assert rows["2024-01-01"]["transfer_ratio"] == 8
    rows, _, errors = capital_dividends._dividend_event_rows(
        [event, {**event, "IMPL_PLAN_PROFILE": "unknown format"}]
    )
    assert errors and not rows  # Do not replace a day with only its parsed half.
    assert not capital_dividends._dividend_event_rows([{**event, "ASSIGN_PROGRESS": "董事会预案"}])[
        0
    ]


def test_cninfo_dividend_request_uses_validated_a_share_code(monkeypatch):
    from scutio_data._providers.akshare import client as akshare_source

    seen = []
    monkeypatch.setattr(
        akshare_source, "fetch", lambda fn, **params: seen.append((fn, params)) or []
    )
    assert capital_dividends._dividend_events("sz300750") == []
    assert seen == [("stock_dividend_cninfo", {"symbol": "300750"})]
    with pytest.raises(ValueError, match="unsupported_market"):
        capital_dividends._dividend_events("hk00700")


def test_corporate_actions_preserves_partial_leg_status():
    partial = {"ok": True, "items": [], "partial": True, "warning": "history truncated"}
    ok = {"ok": True, "items": []}
    env = capital.corporate_actions(
        "300750",
        preloaded={"dividends": partial, "repurchases": ok, "holder_changes": ok, "pledges": ok},
    )
    assert env["ok"] and env["partial"]
    assert env["leg_status"]["dividends"]["warning"] == "history truncated"
