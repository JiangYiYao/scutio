"""Migration boundaries: native account identity, observation dates, units and source gaps."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import scutio_data._documents.filings as documents_filings
import scutio_data._providers.akshare.economic_calendar as calendar_source
import scutio_data._providers.cninfo as providers_cninfo
import scutio_data._providers.eastmoney_special as providers_eastmoney_special
import scutio_data.macro.series as macro_series
from scutio_data import announcements, events, fundamentals, macro
from scutio_data._providers import cninfo
from scutio_data._providers.akshare import client as akshare_source
from scutio_data._providers.akshare.errors import safe_failure


def test_bank_accounts_and_nulls_survive_full_report(monkeypatch):
    native = {
        "SECURITY_CODE": "601398",
        "REPORT_DATE": "2026-06-30",
        "ORG_TYPE": "银行",
        "INTEREST_NI": 300,
        "INTEREST_INCOME": 500,
        "INTEREST_EXPENSE": 200,
        "CUSTOMER_DEPOSIT": 0,
        "UNKNOWN_ACCOUNT": None,
    }
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **kw: [native])
    result = fundamentals.financial_report("601398", period="all", num=1)
    assert result["adapter"] == "akshare" and result["provider"] == "eastmoney"
    row = result["items"][0]
    assert all(row[key] == value for key, value in native.items())
    monkeypatch.setattr(
        akshare_source, "fetch", lambda *a, **kw: [{**native, "SECURITY_CODE": "600519"}]
    )
    assert not fundamentals.financial_report("601398", period="all")["ok"]


def test_macro_periods_and_gold_valuation_are_not_weight(monkeypatch):
    tables = {
        "macro_china_gdp": [{"季度": "2026年第1-2季度", "国内生产总值-同比增长": 4.7}],
        "macro_china_fx_gold": [
            {"月份": "2026年08月份", "黄金储备-数值": 3500.8, "国家外汇储备-数值": 34383.25}
        ],
        "macro_china_xfzxx": [
            {
                "月份": "2026年07月份",
                "消费者信心指数-指数值": 89.2,
                "消费者满意指数-指数值": 90,
                "消费者预期指数-指数值": 90,
            }
        ],
    }
    monkeypatch.setattr(akshare_source, "fetch", lambda fn, **kw: tables[fn])
    gdp = macro.cn_macro_series("gdp_yoy", 1)["items"][0]
    assert gdp["date"] == "2026-06-30" and gdp["date_basis"] == "observation_period"
    gold = macro.cn_macro_series("gold_reserves", 1)["items"][0]
    assert (
        gold["value"] == 3500.8
        and gold["unit"] == "yi_usd"
        and gold["gold_measure"] == "monetary_value"
    )
    confidence = macro.cn_macro_series("consumer_confidence", 1)
    assert confidence["partial"] and confidence["items"][0]["expectation"] is None
    assert confidence["items"][0]["satisfaction"] == 90


def test_nfp_consensus_unit_conversion_and_stale_latest_fallback(monkeypatch):
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda fn, **kw: [{"日期": "2025-08-01", "今值": 7.3, "预测值": 10.6, "前值": 1.4}],
    )
    history = macro_series.consensus_history("us_nfp", limit=1)[0]
    assert history["value"] == 73 and history["forecast"] == 106 and history["prev_value"] == 14
    assert history["stale"]
    calls = []

    def latest(iid, limit):
        calls.append(iid)
        return [{"INDICATOR_ID": iid, "REPORT_DATE": "2026-08-01", "VALUE": 130}]

    monkeypatch.setattr(providers_eastmoney_special, "us_macro_rows", latest)
    row = macro.us_macro_series("us_nfp", 1)["items"][0]
    assert row["value"] == 130 and row["unit"] == "k_persons" and calls == ["EMG00152118"]


def test_fdi_amounts_are_thousands_of_usd_and_monthly_is_not_ytd(monkeypatch):
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **kw: [{"月份": "2023年05月份", "当月": 10850000, "累计": 84350000}],
    )
    result = macro.cn_macro_series("fdi", 1)
    point = result["items"][0]
    assert point["unit"] == "k_usd" and point["value"] == 10850000
    # MOFCOM's cumulative January-May disclosure is 84.35 billion USD.
    assert point["ytd"] * 1000 == 84350000000
    assert point["units"]["ytd"] == "k_usd" and point["unit_reference"]
    assert result["stale"]


def test_completed_repurchase_does_not_report_execution_as_original_plan(monkeypatch):
    from scutio_data import capital
    from scutio_data._providers.akshare import snapshots

    monkeypatch.setattr(
        snapshots,
        "fetch_snapshot",
        lambda *a, **kw: (
            [
                {
                    "股票代码": "600519",
                    "实施进度": "完成实施",
                    "计划回购金额区间-下限": 2999933749.57,
                    "计划回购金额区间-上限": 2999933749.57,
                    "已回购金额": 2999933749.57,
                }
            ],
            {},
        ),
    )
    result = capital.share_repurchases("600519", 1)
    item = result["items"][0]
    assert item["actual_amount"] == 2999933749.57
    assert item["plan_amount_low"] is None and item["plan_amount_high"] is None
    assert not item["plan_amount_available"] and result["partial"]


def test_us_macro_gap_rejects_covered_indicator_before_http():
    with pytest.raises(ValueError, match="does not permit"):
        providers_eastmoney_special.us_macro_rows("EMG00000733", 1)


def test_us_gdp_fallback_preserves_quarterly_frequency(monkeypatch):
    class AuditDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 11, tzinfo=tz)

    monkeypatch.setattr(macro_series, "datetime", AuditDate)
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda fn, **kw: [{"日期": "2024-07-25", "今值": 2.8, "前值": 1.4}],
    )
    monkeypatch.setattr(
        providers_eastmoney_special,
        "us_macro_rows",
        lambda iid, limit: [
            {
                "INDICATOR_ID": iid,
                "REPORT_DATE": "2026-03-01",
                "REPORT_DATE_CH": "2026第1季度",
                "VALUE": 2.0,
            }
        ],
    )
    result = macro.us_macro_series("us_gdp_qoq", 1)
    row = result["items"][0]
    assert row["value"] == 2.0 and row["period"] == "2026第1季度"
    assert row["freq"] == "Q" and row["source"] == "eastmoney_RPT_ECONOMICVALUE_USA"
    assert result["stale"] and result["partial"]


def test_us_latest_actual_recovers_from_failed_jin10_history_without_forecast(monkeypatch):
    from scutio_data._providers.akshare.errors import AKShareError

    def fail(*args, **kwargs):
        raise AKShareError("provider_error", error_type="ValueError")

    monkeypatch.setattr(akshare_source, "fetch", fail)
    monkeypatch.setattr(
        providers_eastmoney_special,
        "us_macro_rows",
        lambda iid, limit: [{"INDICATOR_ID": iid, "REPORT_DATE": "2026-08-01", "VALUE": 0.2}],
    )
    result = macro.us_macro_series("us_cpi_mom", 1)
    assert result["ok"] and result["source"] == "eastmoney_RPT_ECONOMICVALUE_USA"
    assert result["items"][0]["value"] == 0.2 and "forecast" not in result["items"][0]
    assert not macro.macro_surprises(names=["us_cpi_mom"], fallback=False)["ok"]


def test_hk_keyword_search_checks_code_and_link_then_resolves_official_pdf(monkeypatch):
    calls = []

    def fetch(fn, **params):
        calls.append(params)
        return [
            {
                "代码": code,
                "简称": "<em>腾讯控股</em>",
                "公告标题": "2025年年报",
                "公告时间": "2026-04-09",
                "公告链接": f"http://www.cninfo.com.cn/new/disclosure/detail?stockCode={code}&announcementId=1225088088",
            }
            for code in ("09988", "00700")
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    result = providers_cninfo.periodic_reports_hk("hk00700", page_size=1)
    assert result["ok"] and result["total"] == 1 and result["partial"]
    assert calls[0]["symbol"] == "" and calls[0]["keyword"] == "00700"
    item = result["items"][0]
    assert item["sec_code"] == "00700" and item["sec_name"] == "腾讯控股"

    def response(*args, **kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "announcement": {
                    "announcementId": "1225088088",
                    "secCode": "00700",
                    "adjunctUrl": "finalpage/2026-04-09/1225088088.PDF",
                }
            },
        )

    monkeypatch.setattr(cninfo.http, "post", response)
    url, fmt, metadata = documents_filings.resolve_file_url(item)
    assert url.endswith("1225088088.PDF") and fmt == "pdf" and metadata["sec_code"] == "00700"
    with pytest.raises(ValueError, match="identity mismatch"):
        announcements.map_cninfo_announcement_row(
            {
                "代码": "00700",
                "公告链接": "https://www.cninfo.com.cn/?stockCode=09988&announcementId=123",
            },
            market="hk",
        )


def test_baidu_calendar_days_share_budget_and_keep_country_and_forecast(monkeypatch):
    calls = []

    def fetch(fn, **params):
        calls.append((fn, params))
        date = datetime.strptime(params["date"], "%Y%m%d").date().isoformat()
        return [
            {
                "日期": date,
                "时间": "09:30",
                "地区": "中国",
                "事件": "CPI",
                "公布": 0,
                "预期": 0.2,
                "前值": None,
                "重要性": 3,
            }
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    result = macro.economic_calendar("2026-09-09", days=2)
    assert result["ok"] and len(result["items"]) == 2
    assert result["items"][0]["surprise"] == -0.2 and result["items"][0]["previous"] is None
    assert [kw["date"] for fn, kw in calls] == ["20260909", "20260910"]
    assert all(0 < kw["_timeout_seconds"] <= 300 for fn, kw in calls)
    with pytest.raises(ValueError, match="1-14"):
        calendar_source.calendar_rows("2026-09-01", "economic_data", end_day="2026-10-01")


def test_company_event_filter_and_missing_date_rejection(monkeypatch):
    rows = [
        {
            "代码": "600519",
            "简称": "茅台",
            "交易日": "2026-09-09",
            "事件类型": "股东大会",
            "具体事项": "临时会议",
        },
        {"代码": "601398", "简称": "工行", "交易日": "2026-09-09"},
    ]
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: rows)
    result = events.company_events("2026-09-09", code="600519")
    assert result["ok"] and len(result["items"]) == 1 and result["adapter"] == "akshare"
    rows[0]["交易日"] = "2025-09-09"
    assert not events.company_events("2026-09-09")["ok"]


def test_tls_retry_keeps_verification_and_shared_budget(monkeypatch):
    from requests.exceptions import SSLError

    failure = safe_failure(SSLError("private proxy connection details"))
    assert failure["code"] == "tls_error" and "private" not in json.dumps(failure)
    monkeypatch.setenv("SCUTIO_AKSHARE_NETWORK", "auto")
    runner = Mock(
        side_effect=[
            SimpleNamespace(stdout=json.dumps({"ok": False, "failure": failure})),
            SimpleNamespace(stdout=json.dumps({"ok": True, "items": []})),
        ]
    )
    monkeypatch.setattr(akshare_source.subprocess, "run", runner)
    assert akshare_source.fetch("stock_profit_sheet_by_report_em", symbol="SH600519") == []
    first, second = runner.call_args_list
    assert second.kwargs["timeout"] <= first.kwargs["timeout"]
    assert second.kwargs["env"]["NO_PROXY"] == "*"
    assert "verify" not in json.loads(second.kwargs["input"])["params"]
