"""Cross-company financial snapshot contracts; source fixtures never hit HTTP."""

from copy import deepcopy

import pytest
from scutio_data import fundamentals
from scutio_data._providers.akshare import client
from scutio_data._providers.akshare.financial_snapshot import FIELD_CATALOG


@pytest.fixture
def source_row():
    # Public source shape verified with installed AKShare 1.18.94 on 2026-09-22.
    return {
        "序号": 1,
        "股票代码": "600519",
        "股票简称": "贵州茅台",
        "每股收益": 35.57,
        "营业总收入-营业总收入": 92278072083.21,
        "营业总收入-同比增长": 1.3000994756,
        "营业总收入-季度环比增长": -31.3105,
        "净利润-净利润": 44516880421.86,
        "净利润-同比增长": -1.95,
        "净利润-季度环比增长": -36.5904,
        "每股净资产": 200.9897547636,
        "净资产收益率": 16.75,
        "每股经营现金流量": 56.5489085373,
        "销售毛利率": 89.5552128279,
        "所处行业": "白酒Ⅱ",
        "最新公告日期": "2026-08-15T00:00:00.000",
    }


def serve(monkeypatch, rows):
    calls = []

    def fetch(function, **kwargs):
        calls.append((function, kwargs))
        return deepcopy(rows), {
            "snapshot_retrieved_at": 1790082541.1042838,
            "snapshot_cached": False,
            "snapshot_ttl_seconds": 3600,
        }

    monkeypatch.setattr(client, "fetch", fetch)
    return calls


def test_snapshot_keeps_units_fiscal_basis_and_explicit_date(monkeypatch, source_row):
    calls = serve(monkeypatch, [source_row])
    out = fundamentals.financial_snapshot("20260630")
    assert out["ok"] and len(out["items"]) == 1
    assert calls == [("stock_yjbb_em", {"date": "20260630", "_snapshot": True})]
    row = out["items"][0]
    assert row["symbol"] == "sh600519" and row["code"] == "600519"
    assert row["revenue"] == 92278072083.21 and row["roe"] == 16.75
    assert row["net_profit"] == 44516880421.86 and row["eps"] == 35.57
    assert row["net_assets_per_share"] == 200.9897547636
    assert row["operating_cashflow_per_share"] == 56.5489085373
    assert row["report_date"] == "2026-06-30" and row["period_type"] == "ytd"
    assert row["disclosed_at"] == "2026-08-15"
    assert row["report_date_basis"] == "request_filter"
    assert row["disclosed_at_basis"] == "provider_update_date_not_first_disclosure"
    assert row["time_basis"] == "retrospective_current_materials"
    assert row["field_status"] == {}
    assert out["field_catalog"]["net_profit"]["native_field"] == "PARENT_NETPROFIT"
    assert out["field_catalog"]["roe"]["unit"] == "pct"
    assert out["field_catalog"]["eps"]["unit"] == "CNY/share"
    assert out["partial"] and not out["complete"]
    assert not out["coverage"]["listing_membership_verified"]
    assert out["snapshot_retrieved_at"] == 1790082541.1042838


def test_yoy_preserves_source_negative_base_ambiguity(monkeypatch, source_row):
    source_row.update({"净利润-净利润": -5, "净利润-同比增长": 50})
    serve(monkeypatch, [source_row])
    out = fundamentals.financial_snapshot("20260630")
    row = out["items"][0]
    assert row["net_profit"] == -5 and row["net_profit_yoy"] == 50
    assert row["yoy_base_status"] == "not_provided"
    assert out["field_catalog"]["net_profit_yoy"]["basis"] == "provider_reported_base_unknown"
    assert "prior-year base sign is unknown" in out["warning"]


def test_nulls_and_invalid_values_are_not_zeros(monkeypatch, source_row):
    source_row.update(
        {
            "每股收益": 0,
            "净资产收益率": "--",
            "销售毛利率": None,
            "净利润-同比增长": float("inf"),
            "营业总收入-同比增长": True,
            "每股净资产": "broken",
            "最新公告日期": "invalid",
        }
    )
    source_row.pop("每股经营现金流量")
    serve(monkeypatch, [source_row])
    row = fundamentals.financial_snapshot("20260630")["items"][0]
    assert row["eps"] == 0 and "eps" not in row["field_status"]
    assert all(row[field] is None for field in row["field_status"])
    assert row["field_status"] == {
        "disclosed_at": "invalid_date",
        "roe": "missing",
        "gross_margin": "missing",
        "net_profit_yoy": "nonfinite_number",
        "revenue_yoy": "invalid_number",
        "net_assets_per_share": "invalid_number",
        "operating_cashflow_per_share": "missing_column",
    }


def test_code_filter_missing_symbols_and_empty_request(monkeypatch, source_row):
    calls = serve(monkeypatch, [source_row])
    out = fundamentals.financial_snapshot("20260630", codes=["600519", "sh600519", "000001"])
    assert [row["symbol"] for row in out["items"]] == ["sh600519"]
    assert out["missing_symbols"] == ["sz000001"]
    assert out["coverage"]["requested_count"] == 2
    assert len(calls) == 1
    empty = fundamentals.financial_snapshot("20260630", codes=[])
    assert empty["ok"] and empty["items"] == [] and len(calls) == 1


def test_duplicate_conflicts_are_retained_as_field_errors(monkeypatch, source_row):
    duplicate = {**source_row, "序号": 2}
    conflict = {**source_row, "净利润-净利润": 100, "最新公告日期": "2026-08-16"}
    serve(monkeypatch, [source_row, duplicate, conflict, source_row])
    out = fundamentals.financial_snapshot("20260630")
    assert len(out["items"]) == 1 and out["duplicate_rows"] == 3
    row = out["items"][0]
    assert row["net_profit"] is None and row["disclosed_at"] is None
    assert row["field_status"]["net_profit"] == "conflicting_rows"
    assert row["field_status"]["disclosed_at"] == "conflicting_rows"
    assert row["revenue"] == source_row["营业总收入-营业总收入"]


def test_invalid_identities_rejected_but_otc_listing_status_unverified(monkeypatch, source_row):
    rows = [
        {**source_row, "股票代码": "1"},
        {**source_row, "股票代码": "510300"},
        {**source_row, "股票代码": "600519", "symbol": "sz300750"},
        {**source_row, "股票代码": "873989"},
        source_row,
    ]
    serve(monkeypatch, rows)
    out = fundamentals.financial_snapshot("20260630")
    assert out["coverage"]["rejected_count"] == 3
    assert {row["symbol"] for row in out["items"]} == {"bj873989", "sh600519"}
    assert all(row["listing_status"] == "unverified" for row in out["items"])
    assert out["coverage"]["can_include_otc_and_delisted"]


def test_disagreeing_source_report_period_invalidates_metrics(monkeypatch, source_row):
    source_row["REPORTDATE"] = "2025-06-30 00:00:00"
    serve(monkeypatch, [source_row])
    row = fundamentals.financial_snapshot("20260630")["items"][0]
    assert all(row[field] is None for field in FIELD_CATALOG)
    assert {row["field_status"][field] for field in FIELD_CATALOG} == {"report_date_mismatch"}


@pytest.mark.parametrize("period", ["latest", "2026-02-30", "2026-06-29", "20091231", "29991231"])
def test_bad_period_fails_without_network(monkeypatch, period):
    calls = serve(monkeypatch, [])
    out = fundamentals.financial_snapshot(period)
    assert not out["ok"] and out["items"] == [] and not calls


@pytest.mark.parametrize("codes", [["hk00700"], ["usAAPL"], ["sh000001"], "600519"])
def test_bad_code_filter_fails_without_network(monkeypatch, codes):
    calls = serve(monkeypatch, [])
    out = fundamentals.financial_snapshot("20260630", codes=codes)
    assert not out["ok"] and out["items"] == [] and not calls


def test_annual_period_and_future_provider_update_date(monkeypatch, source_row):
    source_row["最新公告日期"] = "2999-01-01"
    serve(monkeypatch, [source_row])
    out = fundamentals.financial_snapshot("20251231")
    row = out["items"][0]
    assert out["period_type"] == row["period_type"] == "annual"
    assert row["field_status"]["disclosed_at"] == "future_provider_update_date"


def test_empty_payload_and_schema_failure_are_distinct(monkeypatch):
    serve(monkeypatch, [])
    empty = fundamentals.financial_snapshot("20260630", codes=["600519"])
    assert empty["ok"] and empty["missing_symbols"] == ["sh600519"]
    serve(monkeypatch, [{"股票代码": "600519", "unknown_field": 1}])
    drift = fundamentals.financial_snapshot("20260630")
    assert not drift["ok"] and "schema" in drift["error"] and drift["items"] == []


def test_provider_failure_never_becomes_successful_empty_screen(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(client, "fetch", fail)
    out = fundamentals.financial_snapshot("20260630")
    assert not out["ok"] and out["items"] == []
    assert out["error"] == "source unavailable" and not out["complete"]
