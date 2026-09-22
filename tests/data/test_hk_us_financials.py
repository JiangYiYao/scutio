"""港/美三表 financial_report。"""

from __future__ import annotations

import pytest


def _statement_line(market, **overrides):
    return {
        "SECURITY_CODE": "00700" if market == "hk" else "AAPL",
        "REPORT_DATE": "2025-12-31",
        "STD_ITEM_NAME" if market == "hk" else "ITEM_NAME": "总资产",
        "STD_ITEM_CODE": "004001",
        "AMOUNT": 100,
        "CURRENCY": "CNY" if market == "hk" else "USD",
        **overrides,
    }


def test_financial_report_hk_pivots_line_items(monkeypatch):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(function, **params):
        assert function == "stock_financial_hk_report_em"
        assert params == {"stock": "00700", "symbol": "利润表", "indicator": "年度"}
        return [
            {
                "REPORT_DATE": day,
                "STD_ITEM_NAME": name,
                "STD_ITEM_CODE": "001" if name == "营业额" else "002",
                "AMOUNT": value,
                "SECURITY_CODE": "00700",
                "SECURITY_NAME_ABBR": "腾讯控股",
            }
            for day, name, value in (
                ("2024-12-31", "营业额", 100),
                ("2024-12-31", "除税后溢利", 20),
                ("2023-12-31", "营业额", 90),
            )
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = fundamentals.financial_report("hk00700", num=2)
    assert out["ok"] and len(out["items"]) == 2
    assert out["items"][0]["营业额"] == 100 and out["items"][0]["除税后溢利"] == 20
    assert out["items"][0]["名称"] == "腾讯控股"
    assert out["items"][0]["_line_items"] == [
        {"field_id": "001", "key": "营业额", "value": 100},
        {"field_id": "002", "key": "除税后溢利", "value": 20},
    ]
    assert [row["报告期"] for row in out["items"]] == ["2024-12-31", "2023-12-31"]


def test_financial_report_us_pivots_and_resolves_secucode(monkeypatch):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    def fetch(function, **params):
        assert function == "stock_financial_us_report_em"
        assert params == {"stock": "TSLA", "symbol": "资产负债表", "indicator": "年报"}
        return [
            {
                "REPORT_DATE": day,
                "ITEM_NAME": "现金及现金等价物",
                "STD_ITEM_CODE": "004001",
                "AMOUNT": value,
                "SECURITY_CODE": "TSLA",
                "SECURITY_NAME_ABBR": "特斯拉",
            }
            for day, value in (("2023-12-31", 1.5e10), ("2024-12-31", 1.6e10))
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = fundamentals.financial_report("usTSLA", report_type="fzb", num=2)
    assert out["ok"] and len(out["items"]) == 2
    assert out["items"][0]["现金及现金等价物"] == 1.6e10
    assert out["items"][0]["名称"] == "特斯拉"
    assert out["items"][0]["_line_items"] == [
        {"field_id": "004001", "key": "现金及现金等价物", "value": 1.6e10}
    ]


def test_us_balance_quarter_uses_latest_disclosed_points(monkeypatch):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    calls = []

    def fetch(function, **params):
        calls.append(params["indicator"])
        data = {
            "单季报": [("2025-12-27", "2026/Q1", 2)],
            "累计季报": [("2026-06-27", "2026/Q9", 4), ("2026-03-28", "2026/Q6", 3)],
            "年报": [("2025-09-27", "2025/FY", 1)],
        }
        return [
            _statement_line("us", REPORT_DATE=day, REPORT=tag, AMOUNT=value)
            for day, tag, value in data[params["indicator"]]
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    out = fundamentals.financial_report("usAAPL", report_type="fzb", num=4, period="quarter")
    assert out["ok"] and calls == ["单季报", "累计季报", "年报"]
    assert [item["报告期"] for item in out["items"]] == [
        "2026-06-27",
        "2026-03-28",
        "2025-12-27",
        "2025-09-27",
    ]


@pytest.mark.parametrize("market", ["hk", "us"])
@pytest.mark.parametrize("identity", ["missing", None, "", "   ", "WRONG"])
@pytest.mark.parametrize("include_valid_row", [False, True])
def test_hk_us_reports_reject_missing_or_wrong_identity(
    monkeypatch, market, identity, include_valid_row
):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    bad = _statement_line(market)
    if identity == "missing":
        bad.pop("SECURITY_CODE")
    else:
        bad["SECURITY_CODE"] = identity
    rows = [_statement_line(market), bad] if include_valid_row else [bad]
    monkeypatch.setattr(akshare_source, "fetch", lambda *args, **kwargs: rows)

    code = "hk00700" if market == "hk" else "usAAPL"
    out = fundamentals.financial_report(code, report_type="fzb", num=1)
    assert not out["ok"]
    assert "identity mismatch" in out["error"]


@pytest.mark.parametrize(
    ("market", "code", "source_code"),
    [("hk", "hk00700", 700), ("hk", "hk00700", "700"), ("us", "usBRK.B", "BRK_B")],
)
def test_hk_us_reports_normalize_valid_identity(monkeypatch, market, code, source_code):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *args, **kwargs: [_statement_line(market, SECURITY_CODE=source_code)],
    )
    out = fundamentals.financial_report(code, report_type="fzb", num=1)
    assert out["ok"]
    assert out["items"][0]["总资产"] == 100


@pytest.mark.parametrize("period", ["all", "报告期", "report", "interim"])
def test_us_reports_reject_all_period_aliases_before_fetch(monkeypatch, period):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    def unexpected_fetch(*args, **kwargs):
        pytest.fail("unsupported period must fail before fetching")

    monkeypatch.setattr(akshare_source, "fetch", unexpected_fetch)
    out = fundamentals.financial_report("usAAPL", period=period)
    assert not out["ok"]
    assert "unsupported for US financial_report" in out["error"]


@pytest.mark.parametrize("market", ["hk", "us"])
def test_hk_us_reports_deduplicate_identical_line_items(monkeypatch, market):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    row = _statement_line(market)
    monkeypatch.setattr(akshare_source, "fetch", lambda *args, **kwargs: [row, dict(row)])
    code = "hk00700" if market == "hk" else "usAAPL"
    out = fundamentals.financial_report(code, report_type="fzb", num=1)
    assert out["ok"] and not out["partial"]
    assert out["items"][0]["总资产"] == 100
    assert out["items"][0]["_line_items"] == [{"field_id": "004001", "key": "总资产", "value": 100}]


@pytest.mark.parametrize("market", ["hk", "us"])
@pytest.mark.parametrize("amounts", [(100, 200), (None, 100), (100, None)])
def test_hk_us_reports_reject_conflicting_line_item_amounts(monkeypatch, market, amounts):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    rows = [_statement_line(market, AMOUNT=value) for value in amounts]
    monkeypatch.setattr(akshare_source, "fetch", lambda *args, **kwargs: rows)
    code = "hk00700" if market == "hk" else "usAAPL"
    out = fundamentals.financial_report(code, report_type="fzb", num=1)
    assert not out["ok"]
    assert "ambiguous financial statement line item" in out["error"]


@pytest.mark.parametrize("market", ["hk", "us"])
def test_hk_us_reports_reject_same_name_with_different_native_ids(monkeypatch, market):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    rows = [_statement_line(market, STD_ITEM_CODE=value) for value in ("004001", "004002")]
    monkeypatch.setattr(akshare_source, "fetch", lambda *args, **kwargs: rows)
    code = "hk00700" if market == "hk" else "usAAPL"
    out = fundamentals.financial_report(code, report_type="fzb", num=1)
    assert not out["ok"]
    assert "ambiguous financial statement line item" in out["error"]


@pytest.mark.parametrize("market", ["hk", "us"])
def test_hk_us_reports_reject_same_native_id_with_different_names(monkeypatch, market):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    name_key = "STD_ITEM_NAME" if market == "hk" else "ITEM_NAME"
    rows = [_statement_line(market, **{name_key: name}) for name in ("总资产", "总负债")]
    monkeypatch.setattr(akshare_source, "fetch", lambda *args, **kwargs: rows)
    code = "hk00700" if market == "hk" else "usAAPL"
    out = fundamentals.financial_report(code, report_type="fzb", num=1)
    assert not out["ok"]
    assert "ambiguous financial statement field ID" in out["error"]


@pytest.mark.parametrize("market", ["hk", "us"])
@pytest.mark.parametrize(
    ("key", "values"),
    [
        ("CURRENCY", ("USD", "CNY")),
        ("ACCOUNT_STANDARD", ("US GAAP", "IFRS")),
        ("START_DATE", ("2025-01-01", "2025-10-01")),
        ("SECURITY_NAME_ABBR", ("Company A", "Company B")),
    ],
)
def test_hk_us_reports_reject_conflicting_period_metadata(monkeypatch, market, key, values):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    rows = [_statement_line(market, **{key: value}) for value in values]
    monkeypatch.setattr(akshare_source, "fetch", lambda *args, **kwargs: rows)
    code = "hk00700" if market == "hk" else "usAAPL"
    out = fundamentals.financial_report(code, report_type="fzb", num=1)
    assert not out["ok"]
    assert "conflicting financial statement metadata" in out["error"]


@pytest.mark.parametrize("year_end_amount", [100, 200])
def test_us_balance_overlap_keeps_all_labels_or_rejects_conflicting_amounts(
    monkeypatch, year_end_amount
):
    from scutio_data import fundamentals
    from scutio_data._providers.akshare import client as akshare_source

    rows = {
        "单季报": [_statement_line("us", REPORT="2025/Q4", REPORT_TYPE="单季报")],
        "累计季报": [],
        "年报": [
            _statement_line("us", REPORT="2025/FY", REPORT_TYPE="年报", AMOUNT=year_end_amount)
        ],
    }
    monkeypatch.setattr(
        akshare_source, "fetch", lambda function, **kwargs: rows[kwargs["indicator"]]
    )
    out = fundamentals.financial_report("usAAPL", report_type="fzb", num=1, period="quarter")
    if year_end_amount != 100:
        assert not out["ok"]
        assert "ambiguous financial statement line item" in out["error"]
        return
    assert out["ok"] and not out["partial"]
    item = out["items"][0]
    assert item["总资产"] == 100
    assert len(item["_line_items"]) == 1
    assert "报告类型" not in item and "报告标签" not in item
    assert item["_source_reports"] == [
        {"REPORT": "2025/Q4", "REPORT_TYPE": "单季报"},
        {"REPORT": "2025/FY", "REPORT_TYPE": "年报"},
    ]


def test_financial_report_zcfzb_alias_still_a_share(monkeypatch):
    from scutio_data import fundamentals

    seen = {}

    def fake_a(code, report_type="lrb", num=8, period="annual"):
        seen["type"] = report_type
        return [{"报告期": "2024-12-31"}]

    monkeypatch.setattr(fundamentals, "_financial_report_a", fake_a)
    fundamentals.financial_report("600519", report_type="zcfzb", num=1)
    # 门面在调用任何上游前统一为真实采用的规范码。
    assert seen["type"] == "fzb"
