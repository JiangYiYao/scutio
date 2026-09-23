"""Listing coverage is independent of successful quotes and retains known gaps."""

import copy
import time
from unittest.mock import Mock

import pytest
from scutio_data import universe
from scutio_data._providers.akshare import client


def _directories():
    return {
        ("stock_info_sh_name_code", "主板A股"): [
            {"证券代码": "600519", "证券简称": "贵州茅台", "上市日期": "2001-08-27T00:00:00.000"},
        ],
        ("stock_info_sh_name_code", "科创板"): [
            {"证券代码": "688001", "证券简称": "华兴源创"},
            {"证券代码": "689009", "证券简称": "九号公司"},
        ],
        ("stock_info_sz_name_code", "A股列表"): [
            {"A股代码": "000001", "A股简称": "平安银行", "板块": "主板"},
            {"A股代码": "300001", "A股简称": "*ST测试", "板块": "创业板", "停牌": True},
        ],
        ("stock_info_bj_name_code", None): [
            {"证券代码": "920000", "证券简称": "安徽凤凰"},
        ],
    }


@pytest.fixture
def directories(monkeypatch):
    data = _directories()

    def fetch(function, *, _snapshot, _timeout_seconds, **params):
        assert _snapshot is True and _timeout_seconds == 45
        result = data[(function, params.get("symbol"))]
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result), {
            "snapshot_retrieved_at": 1789948800,
            "snapshot_cached": True,
            "snapshot_ttl_seconds": 3600,
        }

    monkeypatch.setattr(client, "fetch", Mock(side_effect=fetch))
    return data


def test_all_three_exchanges_with_source_specific_completeness(directories):
    result = universe.stock_universe()
    assert result["ok"] and result["error"] is None and not result["partial"]
    assert result["is_complete"] is None and result["completeness"] == "unknown"
    assert result["returned_count"] == 5
    assert result["errors"] == {}
    assert {row["exchange"] for row in result["items"]} == {"sh", "sz", "bj"}
    for row in result["items"]:
        assert row["symbol"] == row["exchange"] + row["code"]
        assert row["asset_type"] == "a-share" and row["listing_status"] == "listed"
        assert row["source"].startswith("akshare:stock_info_")
        assert row["trading_status"] is None
    coverage = result["coverage"]
    assert coverage["sz"]["source_complete"] is True
    assert coverage["sh"]["source_complete"] is None
    assert coverage["bj"]["source_complete"] is None
    assert all(part["independently_verified"] is False for part in coverage.values())
    assert coverage["sh"]["parts"]["sh_main"]["pagination"]["requested_page_size"] == 10000
    assert coverage["bj"]["parts"]["bj"]["pagination"]["observed_pages"] is None
    assert coverage["sh"]["parts"]["sh_star"]["scope_exclusions"] == {"cdr": 1}
    assert result["historical_membership"] is False
    assert result["as_of_basis"] == "directory_retrieved_at"
    assert client.fetch.call_count == 4


def test_suspended_and_st_names_are_retained_without_quotes(directories):
    result = universe.stock_universe()
    row = next(row for row in result["items"] if row["symbol"] == "sz300001")
    assert row["name"] == "*ST测试"
    assert row["listing_status"] == "listed"


def test_current_szse_302_code_family_is_retained(directories):
    # The official A-share XLSX contains 302132 after its 2025 code change.
    directories[("stock_info_sz_name_code", "A股列表")].append(
        {"A股代码": "302132", "A股简称": "中航成飞", "板块": "创业板"}
    )
    result = universe.stock_universe()
    assert "sz302132" in {row["symbol"] for row in result["items"]}
    assert result["coverage"]["sz"]["source_complete"] is True


def test_as_of_uses_cached_retrieval_time_not_current_clock(directories):
    result = universe.stock_universe()
    assert result["as_of"] == "2026-09-21T00:00:00+00:00"
    assert all(row["as_of"] == result["as_of"] for row in result["items"])
    assert result["items"][1]["listed_date"] == "2001-08-27"


def test_failed_exchange_remains_visible_and_other_directories_survive(directories):
    directories[("stock_info_bj_name_code", None)] = RuntimeError("listing unavailable")
    result = universe.stock_universe()
    assert result["ok"] and result["partial"]
    assert result["is_complete"] is False and result["completeness"] == "partial"
    assert result["errors"] == {"bj": ["listing unavailable"]}
    assert result["coverage"]["bj"]["returned_count"] == 0
    assert result["coverage"]["bj"]["source_complete"] is False
    assert result["coverage"]["bj"]["missing_count"] is None
    assert result["coverage"]["sz"]["source_complete"] is True


def test_one_shanghai_board_failure_does_not_hide_other_board(directories):
    directories[("stock_info_sh_name_code", "科创板")] = RuntimeError("board unavailable")
    result = universe.stock_universe()
    assert result["coverage"]["sh"]["is_complete"] is False
    assert "sh600519" in {row["symbol"] for row in result["items"]}
    assert "sh688001" not in {row["symbol"] for row in result["items"]}


@pytest.mark.parametrize("bad", [[], RuntimeError("offline")])
def test_all_empty_or_failed_is_failure_not_empty_complete_market(directories, bad):
    for key in directories:
        directories[key] = bad
    result = universe.stock_universe()
    assert not result["ok"] and result["items"] == []
    assert result["is_complete"] is False
    assert set(result["errors"]) == {"sh_main", "sh_star", "sz", "bj"}


def test_wrong_exchange_assets_and_bad_identity_rows_are_rejected(directories):
    directories[("stock_info_sh_name_code", "主板A股")].extend(
        [
            {"证券代码": "000001", "证券简称": "深市股票"},
            {"证券代码": "510300", "证券简称": "基金"},
            {"证券代码": "600001", "证券简称": ""},
            {"证券代码": True, "证券简称": "bad"},
            {"证券代码": "sh600002", "证券简称": "bad"},
            "not a record",
        ]
    )
    result = universe.stock_universe()
    assert result["partial"]
    part = result["coverage"]["sh"]["parts"]["sh_main"]
    assert part["invalid_count"] == 6 and part["returned_count"] == 1
    assert part["rejected_rows"][0]["code"] == "000001"
    assert part["rejected_rows"][0]["error"].startswith("unsupported_asset:")
    assert part["source_complete"] is False
    assert {row["symbol"] for row in result["items"] if row["exchange"] == "sh"} == {
        "sh600519",
        "sh688001",
    }


def test_duplicate_conflicting_names_are_not_silently_merged(directories):
    directories[("stock_info_sz_name_code", "A股列表")].extend(
        [
            {"A股代码": "000001", "A股简称": "另一公司"},
            {"A股代码": "000001", "A股简称": "平安银行"},
        ]
    )
    result = universe.stock_universe()
    assert "sz000001" not in {row["symbol"] for row in result["items"]}
    assert result["coverage"]["sz"]["source_complete"] is False
    assert result["coverage"]["sz"]["parts"]["sz"]["duplicate_count"] == 2
    assert result["errors"]["sz"] == ["duplicate_listing_rows"]


def test_page_capacity_reached_is_reported_as_a_known_risk(directories):
    directories[("stock_info_sh_name_code", "主板A股")] *= 10000
    result = universe.stock_universe()
    assert "listing_page_limit_reached" in result["errors"]["sh_main"]
    assert result["coverage"]["sh"]["source_complete"] is False


@pytest.mark.parametrize("market", ["us", "hk", "cn", None])
def test_other_markets_fail_before_network(monkeypatch, market):
    fetch = Mock(side_effect=AssertionError("network must not run"))
    monkeypatch.setattr(client, "fetch", fetch)
    result = universe.stock_universe(market)
    assert not result["ok"] and result["error"].startswith("unsupported_market:")
    fetch.assert_not_called()


def test_exact_directory_requests_use_shared_snapshot_cache(monkeypatch):
    data = _directories()

    def worker(function, params, route, maximum):
        return {"items": data[(function, params.get("symbol"))], "retrieved_at": time.time()}

    worker = Mock(side_effect=worker)
    monkeypatch.setattr(client, "_worker", worker)
    first = universe.stock_universe()
    second = universe.stock_universe()
    assert first["ok"] and second["ok"]
    assert first["items"] == second["items"]
    assert worker.call_count == 4
    assert all(
        part["snapshot_cached"]
        for exchange in second["coverage"].values()
        for part in exchange["parts"].values()
    )
