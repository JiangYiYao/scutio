"""港/美三表 financial_report。"""

from __future__ import annotations


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
            {"REPORT_DATE": day, "REPORT": tag, "ITEM_NAME": "总资产", "AMOUNT": value}
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
