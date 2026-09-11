"""Regression checks for provider units, filing identity, dates and bulk freshness."""

from types import SimpleNamespace

import pytest
import scutio_data._documents.filings as documents_filings
from scutio_data._providers import cninfo


def test_bulk_snapshot_reuses_success_but_never_expired_failure(monkeypatch, tmp_path):
    from scutio_data._providers.akshare import snapshots as snapshots

    monkeypatch.setenv("SCUTIO_CONFIG_DIR", str(tmp_path))
    clock = [10000]
    from scutio_data._providers.akshare import client

    monkeypatch.setattr(client.time, "time", lambda: clock[0])
    calls = []

    def fetch(*a, **k):
        calls.append(a)
        return {"items": [{"股票代码": "600519", "已回购股份数量": 100}], "retrieved_at": clock[0]}

    monkeypatch.setattr(snapshots.akshare_source, "_worker", fetch)
    rows, fresh = snapshots.fetch_snapshot("stock_repurchase_em")
    clock[0] += 100
    cached_rows, cached = snapshots.fetch_snapshot("stock_repurchase_em")
    assert cached_rows == rows and len(calls) == 1
    assert (
        cached["snapshot_cached"]
        and cached["snapshot_retrieved_at"] == fresh["snapshot_retrieved_at"]
    )
    clock[0] += 3600
    monkeypatch.setattr(
        snapshots.akshare_source,
        "_worker",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
    )
    with pytest.raises(RuntimeError, match="down"):
        snapshots.fetch_snapshot("stock_repurchase_em")


def test_bulk_snapshot_version_change_invalidates_cache(monkeypatch, tmp_path):
    from scutio_data._providers.akshare import snapshots as snapshots

    monkeypatch.setenv("SCUTIO_CONFIG_DIR", str(tmp_path))
    versions = ["1.0"]
    monkeypatch.setattr(snapshots.akshare_source, "version", lambda _: versions[0])
    import time

    monkeypatch.setattr(
        snapshots.akshare_source,
        "_worker",
        lambda *a, **k: {"items": [], "retrieved_at": time.time()},
    )
    snapshots.fetch_snapshot("stock_repurchase_em")
    versions[0] = "1.1"
    assert not snapshots.fetch_snapshot("stock_repurchase_em")[1]["snapshot_cached"]


def test_repurchase_identity_sort_units(monkeypatch):
    from scutio_data import capital
    from scutio_data._providers.akshare import snapshots as akshare_snapshots

    rows = [
        {
            "股票代码": code,
            "最新公告日期": day,
            "已回购股份数量": shares,
            "已回购金额": 50000000,
            "实施进度": None,
        }
        for code, day, shares in [
            ("000001", "2026-09-09", 999),
            ("600519", "2025-01-01", 1),
            ("600519", "2026-09-01", 1000000),
        ]
    ]
    monkeypatch.setattr(
        akshare_snapshots, "fetch_snapshot", lambda *a, **k: (rows, {"snapshot_cached": True})
    )
    out = capital.share_repurchases("600519", 1)
    assert out["ok"] and out["items"][0]["actual_shares"] == 1000000
    assert out["items"][0]["actual_amount"] == 50000000 and out["items"][0]["progress"] is None


@pytest.mark.parametrize(
    "kind,index,accum",
    [
        ("hgt", "上证指数", 100),
        ("sgt", "深证指数", 100),
        ("north", "沪深300", 1000000),
        ("south", "沪深300", 1000000),
        ("ggt_sh", "恒生指数", 1000000),
        ("ggt_sz", "恒生指数", 1000000),
    ],
)
def test_connect_scaling_does_not_change_existing_amounts(monkeypatch, kind, index, accum):
    from scutio_data import capital
    from scutio_data._providers.akshare import client as akshare_source

    row = {
        "日期": "2026-09-01",
        "买入成交额": 2,
        "卖出成交额": 1,
        "当日成交净买额": 1,
        "历史累计净买额": 3,
        "当日资金流入": None,
        index: 10000,
    }
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [row])
    out = capital.mutual_connect_daily(kind)
    item = out["items"][0]
    assert item["buy_amt"] == 200 and item["sell_amt"] == 100 and item["deal_amt"] == 300
    assert item["accum_deal_amt"] == 3 * accum
    assert (
        item["fund_inflow"] is None and item["quota_text"] is None and item["index_close"] == 10000
    )
    assert out["partial"]
    expected_unit = "million_hkd" if kind in ("south", "ggt_sh", "ggt_sz") else "million_cny"
    assert out["units"]["net_deal_amt"] == expected_unit
    assert out["units"]["fund_inflow"] == "upstream_unspecified"
    assert out["coverage"]["latest_trade_fields"]["buy_amt"] is True


def test_connect_latest_missing_trade_amounts_are_not_reported_as_available(monkeypatch):
    from scutio_data import capital
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {"日期": "2026-09-10", "买入成交额": None, "卖出成交额": None},
            {"日期": "2024-08-16", "买入成交额": 220.8, "卖出成交额": 246.5},
        ],
    )
    result = capital.mutual_connect_daily("north", page_size=2)
    assert result["ok"] and result["partial"]
    assert result["items"][0]["deal_amt"] is None
    assert result["items"][1]["deal_amt"] == 46730
    assert not any(result["coverage"]["latest_trade_fields"].values())


@pytest.mark.parametrize("direction", ["south", "north"])
def test_connect_detail_preserves_units_and_field_coverage(monkeypatch, direction):
    from scutio_data._providers.akshare import client as akshare_source
    from scutio_data.capital import flows

    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [{"日期": "2026-09-10"}])
    result = getattr(flows, direction + "bound_daily")(page_size=1, detail=True)
    assert result["ok"] and result["partial"]
    assert result["units"]["buy_amt"] == ("million_hkd" if direction == "south" else "million_cny")
    assert not result["coverage"]["total"]["latest_trade_fields"]["buy_amt"]
    assert result["warning"]


def test_holder_basis_is_explicit_and_wrong_identity_fails(monkeypatch):
    from scutio_data import capital
    from scutio_data._providers.akshare import client as akshare_source

    row = {"代码": "000001", "股东户数统计截止日": "2026-09-01", "户均持股数量": 21939.946}
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [row])
    out = capital.holder_num_change("000001")
    assert out["items"][0]["avg_shares"] is None
    assert out["items"][0]["avg_total_shares"] == 21939.946
    assert not capital.holder_num_change("600519")["ok"]


def _announcement(day, ann_id):
    return {
        "代码": "600519",
        "公告时间": day,
        "公告标题": "2025年年度报告",
        "公告链接": f"http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId={ann_id}&announcementTime={day}",
    }


def test_announcements_local_paging_and_detail_resolution(monkeypatch):
    from scutio_data import announcements
    from scutio_data._providers.akshare import client as akshare_source

    rows = [
        _announcement("2026-04-17", "11"),
        _announcement("2025-12-01", "10"),
        _announcement("2026-04-17", "11"),
    ]
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: rows)
    out = announcements.stock_announcements("600519", page_size=1, page_num=2)
    assert out["available_count"] == 2 and out["items"][0]["announcement_id"] == "10"
    assert out["items"][0]["pdf_url"] is None

    def detail(url, **params):
        assert params["params"]["announceId"] == "10"
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "announcement": {
                    "announcementId": "10",
                    "secCode": "600519",
                    "adjunctUrl": "finalpage/2026-01-01/10.PDF",
                }
            },
        )

    monkeypatch.setattr(cninfo.http, "post", detail)
    url, fmt, meta = documents_filings.resolve_file_url(out["items"][0])
    assert (
        url.endswith("/2026-01-01/10.PDF") and fmt == "pdf"
    )  # Path comes from detail, not list date.
    assert meta["sec_code"] == "600519"


@pytest.mark.parametrize(
    "change",
    [
        {"secCode": "000001"},
        {"announcementId": "12"},
        {"adjunctUrl": "https://example.com/wrong.pdf"},
    ],
)
def test_filing_resolver_rejects_mismatched_attachment(monkeypatch, change):
    from scutio_data import announcements

    item = announcements.map_cninfo_announcement_row(_announcement("2026-04-17", "11"))
    detail = {
        "secCode": "600519",
        "announcementId": "11",
        "adjunctUrl": "finalpage/2026-04-17/11.PDF",
        **change,
    }
    monkeypatch.setattr(
        cninfo.http,
        "post",
        lambda *a, **k: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"announcement": detail}
        ),
    )
    with pytest.raises(ValueError):
        cninfo.resolve_cninfo_file(item)


def test_lockup_partition_and_no_extra_scaling(monkeypatch):
    from scutio_data import announcements
    from scutio_data._providers.akshare import client as akshare_source

    rows = [
        {
            "解禁时间": day,
            "实际解禁数量": 547182073,
            "解禁数量": 547182073,
            "占流通市值比例": 0.2736508909,
        }
        for day in ("2026-09-08", "2026-09-09", "2026-12-09")
    ]
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: rows)
    out = announcements.lockup_expiry("688981", "2026-09-09", forward_days=90)
    assert len(out["history"]) == len(out["upcoming"]) == 1
    assert out["upcoming"][0]["shares"] == 547182073 and out["upcoming"][0]["ratio"] == 0.2736508909
    assert not announcements.lockup_expiry("688981", "2026-09-09", -1)["ok"]


def test_consensus_never_treats_analyst_count_as_eps(monkeypatch):
    from scutio_data import research
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [{"年度": "2026", "预测机构数": 48, "行业平均数": 7.71}],
    )
    assert not research.consensus_forecast("600519")["ok"]
    monkeypatch.setattr(
        akshare_source, "fetch", lambda *a, **k: [{"年度": "2026", "预测机构数": 48, "均值": None}]
    )
    out = research.consensus_forecast("600519")
    assert out["ok"] and out["items"][0]["eps"] is None and out["items"][0]["analyst_count"] == 48


def test_valuation_preserves_yuan_and_nonfinite_missing(monkeypatch):
    from scutio_data import valuation
    from scutio_data._providers.akshare import client as akshare_source

    row = dict.fromkeys(valuation._AK_VALUE_MAP, None)
    row.update(
        {
            "数据日期": "2026-09-01T00:00:00",
            "总市值": 614891215441.56,
            "PE(TTM)": 305.36554511,
            "市净率": float("nan"),
        }
    )
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [row])
    item = valuation._eastmoney_history("688981")[0]
    assert (
        item["total_mcap"] == 614891215441.56
        and item["pe_ttm"] == 305.36554511
        and item["pb"] is None
    )
    row.pop("总股本")
    with pytest.raises(ValueError, match="schema"):
        valuation._eastmoney_history("688981")
