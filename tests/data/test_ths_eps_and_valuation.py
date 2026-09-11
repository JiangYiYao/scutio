"""同花顺 EPS 表解析 + 报价侧 valuation_snapshot。"""

from __future__ import annotations

import pytest


def _quote_600519_env():
    """``security_quote`` 风格信封（valuation_snapshot 报价入口）。"""
    row = {
        "name": "贵州茅台",
        "price": 1361.76,
        "mcap_yi": 17000.0,
        "pe_ttm": 20.5,
        "pb": 7.3,
        "code": "600519",
        "symbol": "sh600519",
        "source": "tencent",
    }
    return {
        "ok": True,
        "partial": False,
        "error": None,
        "errors": {},
        "sources_used": ["tencent"],
        "missing": [],
        "quotes": {"sh600519": row, "600519": row},
    }


def test_ths_eps_forecast_preserves_provider_columns(monkeypatch):
    from scutio_data import research
    from scutio_data._providers.akshare import client as akshare_source

    rows = [{"年度": "2026", "预测机构数": 48, "均值": 68.70, "最大值": 77.05, "行业平均数": 7.71}]

    def fake(function, **params):
        assert function == "stock_profit_forecast_ths" and params["symbol"] == "600519"
        return rows

    monkeypatch.setattr(akshare_source, "fetch", fake)
    env = research.eps_forecast("600519")
    assert env["ok"] and env["items"] == rows and env["adapter"] == "akshare"


def test_ths_eps_forecast_empty_when_no_coverage(monkeypatch):
    from scutio_data import research
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [])
    env = research.eps_forecast("600519")
    assert env["ok"] and env["items"] == []


def test_valuation_snapshot_quote_only_no_eps_fields(monkeypatch):
    """快照仅报价侧，不含一致预期字段。"""
    from scutio_data import valuation

    monkeypatch.setattr(valuation, "security_quote", lambda codes: _quote_600519_env())
    out = valuation.valuation_snapshot("600519")
    assert out["ok"] is True
    assert out["price"] == 1361.76
    assert out["pe_ttm"] == 20.5
    assert out["pb"] == 7.3
    assert out["source"] == "tencent"
    assert out.get("quote_source") == "tencent"
    assert "eps_cur" not in out
    assert "eps_error" not in out
    assert "pe_fwd" not in out
    assert "peg" not in out


def test_valuation_snapshot_reuses_preloaded_quote(monkeypatch):
    from scutio_data import valuation

    monkeypatch.setattr(
        valuation,
        "security_quote",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not refetch quote")),
    )
    out = valuation.valuation_snapshot("600519", quote_env=_quote_600519_env())
    assert out["ok"] is True
    assert out["price"] == 1361.76


def test_valuation_snapshot_uses_security_quote_fallback_when_tencent_empty(monkeypatch):
    """报价门面链：tencent 空 → 下一源有价时 valuation_snapshot 仍可用。"""
    from scutio_data import valuation

    def fake_sq(codes):
        return {
            "ok": True,
            "partial": False,
            "error": None,
            "errors": {"tencent": "empty"},
            "sources_used": ["tencent", "sina"],
            "missing": [],
            "quotes": {
                "sh600519": {
                    "name": "贵州茅台",
                    "price": 1500.0,
                    "last_close": 1490.0,
                    "mcap_yi": 1.0,
                    "pe_ttm": 20.0,
                    "pb": 7.0,
                    "code": "600519",
                    "symbol": "sh600519",
                    "source": "sina",
                }
            },
        }

    monkeypatch.setattr(valuation, "security_quote", fake_sq)
    out = valuation.valuation_snapshot("600519")
    assert out["ok"] is True
    assert out["price"] == 1500.0
    assert out.get("quote_source") == "sina"
    assert out["source"] == "sina"


def test_valuation_history_returns_compact_windows_without_series(monkeypatch):
    from scutio_data import valuation

    rows = [
        {"date": "2020-01-02", "pe_ttm": 10.0, "pb": 1.0, "ps": 2.0},
        {"date": "2022-12-30", "pe_ttm": 20.0, "pb": 2.0, "ps": 3.0},
        {"date": "2024-12-31", "pe_ttm": 30.0, "pb": 3.0, "ps": 4.0},
        {"date": "2026-08-11", "pe_ttm": 40.0, "pb": 4.0, "ps": 5.0},
    ]
    monkeypatch.setattr(valuation, "_eastmoney_history", lambda code: rows)
    monkeypatch.setattr(
        valuation,
        "_baidu_history",
        lambda code: (_ for _ in ()).throw(AssertionError("backup must not run")),
    )

    out = valuation.valuation_history("688981", include_series=False)

    assert out["ok"] is True
    assert out["source"] == "eastmoney_value_analysis"
    assert out["sample_count"] == 4
    assert out["items"] is None
    assert out["series_included"] is False
    assert out["windows"]["all"]["metrics"]["pe_ttm"]["percentile"] == 1.0
    assert out["windows"]["3y"]["sample_count"] == 2


def test_valuation_history_falls_back_only_after_primary_failure(monkeypatch):
    from scutio_data import valuation

    monkeypatch.setattr(
        valuation,
        "_eastmoney_history",
        lambda code: (_ for _ in ()).throw(RuntimeError("eastmoney down")),
    )
    monkeypatch.setattr(
        valuation,
        "_baidu_history",
        lambda code: (
            [
                {"date": "2025-08-11", "pe_ttm": 20.0, "pb": 2.0},
                {"date": "2026-08-11", "pe_ttm": 30.0, "pb": 3.0},
            ],
            {"pb": "one indicator partial"},
        ),
    )

    out = valuation.valuation_history("688981")

    assert out["ok"] is True
    assert out["source"] == "baidu_valuation_backup"
    assert out["partial"] is True
    assert out["errors"]["eastmoney"] == "eastmoney down"
    assert out["errors"]["baidu_pb"] == "one indicator partial"
    assert len(out["items"]) == 2


def test_valuation_history_rejects_non_a_before_fetch(monkeypatch):
    from scutio_data import valuation

    monkeypatch.setattr(
        valuation,
        "_eastmoney_history",
        lambda code: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    out = valuation.valuation_history("usTSM")
    assert out["ok"] is False
    assert "unsupported_market" in out["error"]


def test_forward_pe_and_peg_pure_formulas():
    from scutio_data.valuation import calc_peg, forward_pe, pe_digestion

    assert forward_pe(100, 10) == 10.0
    assert forward_pe(100, 0) == float("inf")
    assert calc_peg(20, 0.2) == pytest.approx(1.0)
    assert pe_digestion(30, 0.1) == 0.0


@pytest.mark.parametrize("code", ["sh000001", "000001.SH", "sz399001", "sh510300", "sh110093"])
def test_consensus_rejects_non_company_assets_before_fetch(monkeypatch, code):
    from unittest.mock import Mock

    from scutio_data import research
    from scutio_data._providers.akshare import client

    fetch = Mock(side_effect=AssertionError("must not request another security"))
    monkeypatch.setattr(client, "fetch", fetch)
    result = research.consensus_forecast(code)
    assert result["ok"] is False
    assert result["error_code"] == "unsupported_asset"
    fetch.assert_not_called()
