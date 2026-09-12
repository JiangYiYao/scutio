"""Offline schema contracts for canonical canonical endpoints."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import scutio_data._documents.filings as documents_filings
import scutio_data._documents.report_paths as documents_report_paths
import scutio_data._documents.text as documents_text
import scutio_data._providers.cninfo as providers_cninfo
import scutio_data._providers.eastmoney as providers_eastmoney
import scutio_data._providers.sec as providers_sec
import scutio_data.capital.dividends as capital_dividends
from scutio_data._providers import cninfo
from scutio_data._providers import quotes as quote_source
from scutio_data._providers.akshare import market as akshare_market


def response(*, data=None, text=""):
    """Build a small requests-compatible response stub."""
    return SimpleNamespace(
        json=lambda: data, text=text, encoding=None, raise_for_status=lambda: None
    )


def test_split_code_honors_explicit_exchange():
    """Explicit SH/SZ/BJ markers must not be re-inferred away."""
    from scutio_data._runtime.symbols import get_prefix, normalize_code, split_code

    assert split_code("sh000001") == ("sh", "000001")
    assert split_code("000001.SH") == ("sh", "000001")
    assert split_code("SH.000001") == ("sh", "000001")
    assert split_code("sh000300") == ("sh", "000300")
    assert split_code("sh000688") == ("sh", "000688")
    assert get_prefix("sh000001") == "sh"
    assert normalize_code("sh000001") == "000001"
    # Bare 000xxx stays stock-oriented (SZ) — not 上证指数.
    assert split_code("000001") == ("sz", "000001")
    assert split_code("399001") == ("sz", "399001")
    assert split_code("600519") == ("sh", "600519")
    assert split_code("899050") == ("bj", "899050")


def test_split_code_hong_kong_markers():
    """HK codes need explicit markers; pad to 5 digits for Tencent."""
    import pytest
    from scutio_data._runtime.symbols import get_prefix, normalize_code, split_code

    assert split_code("hk02513") == ("hk", "02513")
    assert split_code("HK02513") == ("hk", "02513")
    assert split_code("02513.HK") == ("hk", "02513")
    assert split_code("HK.02513") == ("hk", "02513")
    assert split_code("hk700") == ("hk", "00700")
    assert split_code("00700.HK") == ("hk", "00700")
    assert get_prefix("02513.HK") == "hk"
    assert normalize_code("hk02513") == "02513"
    # Bare 5-digit must not silently become HK (would collide with ambiguity).
    with pytest.raises(ValueError):
        split_code("02513")


def test_parse_news_time_and_rank():
    """本地时间解析 + 噪音过滤 + time/relevance 排序。"""
    from scutio_data.feeds import parse_news_time, rank_stock_news_items

    assert parse_news_time("2026-07-31 15:30:00").year == 2026
    assert parse_news_time("2026/07/31 15:30").month == 7
    assert parse_news_time("") is None
    assert parse_news_time(None) is None

    raw = [
        {
            "title": "30只个股突破半年线",
            "content": "含 600519 等",
            "time": "2026-08-01 10:00:00",
            "source": "榜",
            "url": "u0",
        },
        {
            "title": "贵州茅台发布半年报",
            "content": "营收增长",
            "time": "2026-07-30 09:00:00",
            "source": "M",
            "url": "u1",
        },
        {
            "title": "600519 获机构调研",
            "content": "互动",
            "time": "2026-07-31 12:00:00",
            "source": "M",
            "url": "u2",
        },
    ]
    by_time = rank_stock_news_items(raw, "600519", name="贵州茅台", order="time", drop_noise=True)
    assert len(by_time) == 2
    assert by_time[0]["url"] == "u2"
    assert by_time[1]["url"] == "u1"
    assert "relevance" in by_time[0]

    by_rel = rank_stock_news_items(
        raw, "600519", name="贵州茅台", order="relevance", drop_noise=True
    )
    # 标题含名/码的优先于仅时间新的
    assert by_rel[0]["url"] in ("u1", "u2")
    assert by_rel[0]["relevance"] >= by_rel[1]["relevance"]

    kept_noise = rank_stock_news_items(raw, "600519", order="time", drop_noise=False, page_size=10)
    assert len(kept_noise) == 3


def test_news_contracts(monkeypatch):
    """News parser preserves normalized fields + relevance/order 信封。"""
    from scutio_data import feeds
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {
                "新闻标题": "<b>T</b>",
                "新闻内容": "<p>C</p>",
                "发布时间": "2026-07-31 10:00:00",
                "文章来源": "M",
                "新闻链接": "U",
            }
        ],
    )
    news_out = feeds.stock_news("600000")
    assert news_out["ok"] is True
    assert news_out.get("order") == "time"
    assert news_out.get("drop_noise") is True
    assert news_out["fetched_raw"] == 1
    assert news_out["after_filter"] == 1
    assert news_out["filtered_out"] == 0
    assert news_out["returned"] == 1
    assert news_out["empty_reason"] is None
    assert news_out["items"] == [
        {
            "title": "T",
            "content": "C",
            "time": "2026-07-31 10:00:00",
            "source": "M",
            "url": "U",
            "relevance": 0,
        }
    ]

    # order=relevance 仍返回规范化 items
    news_rel = feeds.stock_news("600000", order="relevance", name="浦发银行")
    assert news_rel["ok"] is True
    assert news_rel.get("order") == "relevance"
    assert news_rel["items"][0]["title"] == "T"

    # 港美代码只在查询时取 pure；排序阶段必须保留完整市场身份，不能二次校验裸码。
    assert feeds.stock_news("hk01810", name="小米集团-W")["ok"] is True
    assert feeds.stock_news("usAAPL", name="苹果")["ok"] is True

    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: [])
    empty = feeds.stock_news("hk01810", name="小米集团-W")
    assert empty["ok"] is True
    assert empty["items"] == []
    assert empty["fetched_raw"] == 0
    assert empty["filtered_out"] == 0
    assert empty["empty_reason"] == "upstream_empty"

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {
                "新闻标题": "30只个股突破半年线",
                "新闻内容": "含01810",
                "发布时间": "2026-08-08 10:00:00",
                "文章来源": "榜单",
                "新闻链接": "U",
            }
        ],
    )
    filtered = feeds.stock_news("hk01810", name="小米集团-W")
    assert filtered["items"] == []
    assert filtered["fetched_raw"] == 1
    assert filtered["after_filter"] == 0
    assert filtered["filtered_out"] == 1
    assert filtered["empty_reason"] == "all_filtered"


def test_fundamental_and_flow_contracts(monkeypatch):
    """Fundamental, margin, block-trade, dividend and flow fields stay stable."""
    from scutio_data import capital, fundamentals
    from scutio_data._providers.akshare import client as akshare_source
    from scutio_data._runtime.results import envelope_items

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {"item": key, "value": value}
            for key, value in {
                "股票代码": "600000",
                "股票简称": "P",
                "行业": "银行",
                "总市值": 100,
            }.items()
        ],
    )
    info = fundamentals.stock_info("sh600000")
    assert info["ok"] is True
    assert info["code"] == "600000"
    assert info["industry"] == "银行"
    assert info.get("source") == "akshare_eastmoney"

    seen = []

    def fetch_statement(fn, **kw):
        seen.append(fn)
        return [
            {
                "SECURITY_CODE": "600000",
                "REPORT_DATE": day,
                "NETPROFIT": value,
                "NETPROFIT_YOY": 5,
                "EMPTY": None,
            }
            for day, value in [("2025-12-31", 10), ("2025-09-30", 8)]
        ]

    monkeypatch.setattr(akshare_source, "fetch", fetch_statement)
    statement = fundamentals.financial_report("600000")
    assert statement["ok"] and statement["items"][0]["NETPROFIT"] == 10
    assert statement["items"][0]["EMPTY"] is None
    assert [x["报告期"] for x in statement["items"]] == ["2025-12-31"]
    assert len(fundamentals.financial_report("600000", period="all")["items"]) == 2
    fundamentals.financial_report("600000", report_type="zcfzb")
    fundamentals.financial_report("600000", report_type="资产负债表")
    assert seen[-2:] == ["stock_balance_sheet_by_report_em"] * 2

    from scutio_data._providers.akshare import snapshots as akshare_snapshots

    def snapshot(function, **params):
        if function == "tool_trade_date_hist_sina":
            return [{"trade_date": "2026-01-05"}], {}
        if function == "stock_margin_detail_sse":
            return [{"信用交易日期": "20260105", "标的证券代码": "600000", "融资余额": 2}], {}
        return [{"交易日期": "2026-01-05", "证券代码": "600000", "收盘价": 10, "成交价": 11}], {}

    monkeypatch.setattr(akshare_snapshots, "fetch_snapshot", snapshot)
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        capital_dividends,
        "_dividend_events",
        lambda code: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [{"除权除息日": "2026-01-02", "现金分红-现金分红比例": 1}],
    )
    assert (
        envelope_items(capital.margin_trading("600000", 1, end_date="2026-01-05"))[0]["rzye"] == 2
    )
    assert (
        envelope_items(capital.block_trade("600000", 1, end_date="2026-01-05"))[0]["premium_pct"]
        == 10.0
    )
    assert envelope_items(capital.dividend_history("600000"))[0]["bonus_rmb"] == 1
    monkeypatch.setattr(
        akshare_source, "fetch", lambda *a, **k: [{"日期": "2026-01-01", "主力净流入-净额": 3}]
    )
    flow = capital.stock_fund_flow_120d("600000")
    assert envelope_items(flow)[0]["date"] == "2026-01-01"
    assert flow["items"][0]["main_net"] == 3 and flow["partial"]


def test_dragon_tiger_daily_contract(monkeypatch):
    """The public capital facade preserves the source's money units."""
    from scutio_data import capital
    from scutio_data._providers.akshare import snapshots

    monkeypatch.setattr(
        snapshots,
        "fetch_snapshot",
        lambda *a, **k: ([{"上榜日": "2026-01-01", "代码": "600000", "龙虎榜净买额": 10000}], {}),
    )
    dragon = capital.daily_dragon_tiger("2026-01-01")
    assert dragon["stocks"][0]["net_buy_wan"] == 1.0


def test_eastmoney_quote_bare_code_only_when_unambiguous(monkeypatch):
    """裸码索引与 index_quote_rows 一致：无歧义才挂 pure。"""

    class FakeResp:
        def json(self):
            return {
                "data": {
                    "f57": "600519",
                    "f58": "N",
                    "f43": 10.0,
                    "f60": 9.0,
                    "f169": 1.0,
                    "f170": 1.0,
                    "f46": 9.5,
                    "f44": 10.5,
                    "f45": 9.0,
                    "f47": 100,
                    "f48": 1000,
                    "f168": 1.0,
                    "f116": 1e9,
                    "f117": 1e9,
                    "f50": 1.0,
                }
            }

    monkeypatch.setattr(providers_eastmoney, "em_get", lambda *a, **k: FakeResp())
    out = quote_source.eastmoney_quote(["600519"])
    assert "sh600519" in out or "600519" in out
    # 单票无歧义时应有裸码
    assert "600519" in out
    assert out["600519"]["price"] == 10.0


def test_daily_fund_flow_is_chronological_and_preserves_null_and_zero(monkeypatch):
    from scutio_data import capital
    from scutio_data._providers.akshare import client as akshare_source

    rows = [
        {"日期": "2026-08-05", "主力净流入-净额": None},
        {"日期": "2026-08-04", "主力净流入-净额": 0},
    ]
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: rows)
    result = capital.stock_fund_flow_120d("600000")
    assert result["ok"] and result["partial"] and result["adapter"] == "akshare"
    assert [row["date"] for row in result["items"]] == ["2026-08-04", "2026-08-05"]
    assert [row["main_net"] for row in result["items"]] == [0, None]


def test_industry_comparison_fetches_gainers_and_losers(monkeypatch):
    """Full-table sorting must find the real losers and preserve classification."""
    from scutio_data import capital
    from scutio_data._providers.akshare import client as akshare_source

    rows = [
        {"板块名称": name, "涨跌幅": pct, "上涨家数": 1, "下跌家数": 3, "领涨股票-涨跌幅": 9}
        for name, pct in [("强势A", 5), ("最弱X", -8), ("强势B", 4), ("次弱Y", -6)]
    ]
    monkeypatch.setattr(akshare_source, "fetch", lambda *a, **k: rows)
    out = capital.industry_comparison(2)
    assert out["ok"] and not out["partial"]
    assert [row["name"] for row in out["top"]] == ["强势A", "强势B"]
    assert [row["name"] for row in out["bottom"]] == ["最弱X", "次弱Y"]
    assert out["total"] == 4 and out["classification"] == "eastmoney"
    assert out["top"][0]["leader_change"] == 9


def test_industry_comparison_partial_success(monkeypatch):
    """Invalid numeric rows cannot enter a ranking as real zero returns."""
    from scutio_data import capital
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {"板块名称": "valid", "涨跌幅": -1},
            {"板块名称": "missing", "涨跌幅": None},
        ],
    )
    out = capital.industry_comparison(2)
    assert out["ok"] and out["total"] == 1
    assert out["top"][0]["name"] == "valid"


def test_industry_comparison_both_legs_fail(monkeypatch):
    from scutio_data import capital
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source, "fetch", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))
    )
    out = capital.industry_comparison()
    assert not out["ok"] and out["top"] == []
    assert set(out["errors"]) == {"eastmoney", "sina"}


def test_industry_comparison_sina_fallback(monkeypatch):
    """Company count is not the number of advancing stocks."""
    from scutio_data import capital
    from scutio_data._providers.akshare import client as akshare_source

    def fake(function, **params):
        if function == "stock_board_industry_name_em":
            raise ConnectionError("down")
        return [
            {
                "板块": "玻璃",
                "label": "new_blhy",
                "公司家数": 19,
                "涨跌幅": 1.2,
                "个股涨跌幅": 3.7,
                "股票名称": "华建集团",
            }
        ]

    monkeypatch.setattr(akshare_source, "fetch", fake)
    out = capital.industry_comparison(1)
    assert out["ok"] and out["partial"] and out["classification"] == "sina"
    row = out["top"][0]
    assert row["constituent_count"] == 19 and row["up_count"] is None and row["down_count"] is None
    assert row["leader_change"] == 3.7


def test_security_quote_and_bars_fallback_envelope(monkeypatch):
    """Fallback helpers expose ok/partial and isolate per-source failures."""
    from scutio_data import market

    monkeypatch.setattr(
        quote_source,
        "tencent_quote",
        lambda codes: {
            "sh600519": {
                "name": "贵州茅台",
                "price": 100.0,
                "last_close": 99.0,
                "code": "600519",
                "symbol": "sh600519",
                "source": "tencent",
            }
        },
    )
    monkeypatch.setattr(
        quote_source,
        "sina_quote",
        lambda codes: {
            "sz000001": {
                "name": "",
                "price": 10.0,
                "last_close": 10.0,
                "code": "000001",
                "symbol": "sz000001",
                "source": "sina",
            }
        },
    )
    result = market.security_quote(["600519", "000001"])
    assert result["ok"] is True
    assert "sh600519" in result["quotes"] or "600519" in result["quotes"]
    assert "tencent" in result["sources_used"]

    def bars_fetch(*args, source, **kwargs):
        if source == "sina":
            raise RuntimeError("sina down")
        return [
            {
                "datetime": "2026-07-20",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 100,
                "amount": 1000,
            }
        ]

    monkeypatch.setattr(akshare_market, "bars", bars_fetch)
    bars = market.security_bars("600519", count=1, sources=("akshare_sina", "akshare_eastmoney"))
    assert bars["ok"] and bars["source"] == "akshare_eastmoney"
    assert bars["bars"][0]["close"] == 1.5
    assert "akshare_sina" in bars["errors"]


def test_mutual_type_and_southbound_mapping():
    """港股通/南向 type codes stay stable."""
    from scutio_data.capital.flows import MUTUAL_TYPE, _mutual_type_code

    assert _mutual_type_code("south") == "006"
    assert _mutual_type_code("港股通") == "006"
    assert _mutual_type_code("ggt_sh") == "002"
    assert _mutual_type_code("north") == "005"
    assert MUTUAL_TYPE["sgt"] == "003"


def test_tencent_quote_prefers_explicit_index_prefix(monkeypatch):
    """sh000001 must request the Shanghai composite, not sz000001."""

    seen = {}

    class FakeResp:
        def read(self):
            # Minimal Tencent line for sh000001
            body = 'v_sh000001="1~上证指数~000001~3796.28~3764.15~3791.66~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~32.13~0.85~3831.66~3741.11~0~0~129465190~0~0~0~0~0~0~2.41~0~0~0~-1~-1~0~0~0~0";'
            return body.encode("gbk")

    def fake_bytes(url, **kwargs):
        seen["url"] = url
        return FakeResp().read().decode("gbk")

    monkeypatch.setattr(quote_source, "_http_get_bytes", fake_bytes)
    out = quote_source.tencent_quote(["sh000001"])
    assert "sh000001" in seen["url"]
    assert "sz000001" not in seen["url"]
    assert out["sh000001"]["name"] == "上证指数"
    assert out["000001"]["name"] == "上证指数"
    assert out["sh000001"]["exchange"] == "sh"


def test_tencent_quote_keeps_colliding_index_and_stock(monkeypatch):
    """sh000001 and bare 000001 must not overwrite each other."""

    class FakeResp:
        def read(self):
            return (
                'v_sh000001="1~上证指数~000001~1~1~1~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~1~0.1~1~1~0~0~1~0~0~0~0~0~0~1~0~0~0~-1~-1~0~0~0~0";'
                'v_sz000001="1~平安银行~000001~2~2~2~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~1~1.0~2~2~0~0~1~0~0~0~0~0~0~1~0~0~0~1~1~0~0~0~0";'
            ).encode("gbk")

    monkeypatch.setattr(
        quote_source, "_http_get_bytes", lambda *a, **k: FakeResp().read().decode("gbk")
    )
    out = quote_source.tencent_quote(["sh000001", "000001"])
    assert out["sh000001"]["name"] == "上证指数"
    assert out["sz000001"]["name"] == "平安银行"
    assert "000001" not in out  # ambiguous bare code omitted


def test_announcements_contracts(monkeypatch):
    """CNInfo announcements keep identity, title, and original-document resolution."""
    from scutio_data import announcements
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [_ak_announcement("A", "2026-01-01", "1", "601318")],
    )
    result = announcements.stock_announcements("601318")
    assert result["ok"] and result["items"][0]["title"] == "A"
    assert "announcementId=1" in result["items"][0]["url"]
    assert result["items"][0]["pdf_url"] is None
    assert result["items"][0]["file_resolution"] == "cninfo_detail"


def _ak_announcement(title, day, ann_id, code="600519"):
    return {
        "公告标题": title,
        "公告时间": day,
        "代码": code,
        "简称": "测试",
        "公告链接": f"https://www.cninfo.com.cn/new/disclosure/detail?stockCode={code}&announcementId={ann_id}&announcementTime={day}",
    }


def test_periodic_reports_and_download_pdf(monkeypatch, tmp_path):
    """定期报告筛选 + PDF 落盘（%PDF 魔数校验）+ 旁路 txt。"""
    from pathlib import Path

    from scutio_data import announcements
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [_ak_announcement("茅台2025年年报", "2026-04-17", "99")],
    )
    monkeypatch.setattr(
        cninfo.http,
        "post",
        lambda *a, **k: response(
            data={
                "announcement": {
                    "announcementId": "99",
                    "secCode": "600519",
                    "adjunctUrl": "finalpage/2026-04-17/99.PDF",
                    "adjunctType": "PDF",
                }
            }
        ),
    )
    env = announcements.periodic_reports("600519", kind="annual", page_size=5)
    assert env["ok"] is True
    assert env["items"][0]["kind"] == "annual"
    assert env["items"][0]["pdf_url"] is None
    assert documents_filings.resolve_file_url(env["items"][0])[0].endswith("/99.PDF")

    monkeypatch.setattr(
        documents_filings,
        "download_filing_bytes",
        lambda url, **kw: b"%PDF-1.7 fake content for test with some text",
    )
    monkeypatch.setattr(
        documents_text,
        "extract_text_from_bytes",
        lambda content, file_format="": "extracted body",
    )
    dl = announcements.download_announcement_pdf(env["items"][0], target_dir=str(tmp_path))
    assert dl["ok"] is True
    assert dl["path"]
    assert Path(dl["path"]).exists()
    assert dl["bytes"] > 4
    assert dl.get("text_path")
    assert Path(dl["text_path"]).exists()
    assert "extracted" in Path(dl["text_path"]).read_text(encoding="utf-8")

    monkeypatch.setattr(
        documents_filings,
        "download_filing_bytes",
        lambda url, **kw: b"<html>not pdf</html>",
    )
    bad = announcements.download_announcement_pdf(
        {"pdf_url": "https://example.com/x.pdf", "market": "a"},
        target_dir=str(tmp_path / "c"),
    )
    assert bad["ok"] is False

    monkeypatch.setattr(
        documents_filings,
        "download_filing_bytes",
        lambda url, **kw: b"%PDF-1.7 safe filename test" * 30,
    )
    safe_dir = tmp_path / "safe"
    escaped = announcements.download_announcement_pdf(
        {"pdf_url": "https://example.com/x.pdf", "market": "a"},
        target_dir=str(safe_dir),
        filename="../../escape.pdf",
        extract_text=False,
    )
    assert escaped["ok"] is True
    assert Path(escaped["path"]).parent == safe_dir
    assert not (tmp_path / "escape.pdf").exists()


def test_a_periodic_reports_prefers_full_chinese_and_keeps_partial_results(monkeypatch):
    """完整中文报告优先；某一栏目失败时仍保留其他栏目的结果。"""
    from datetime import datetime

    from scutio_data import announcements
    from scutio_data._providers.akshare import client as akshare_source
    from scutio_data._runtime.environment import CN_TZ

    def row(title, timestamp, announcement_id):
        day = datetime.fromtimestamp(timestamp / 1000, tz=CN_TZ).strftime("%Y-%m-%d")
        return _ak_announcement(title, day, announcement_id)

    def fake_query(function, **kwargs):
        category = kwargs.get("category") or ""
        if category == "半年报":
            raise RuntimeError("semi unavailable")
        if category == "一季报":
            return [
                row("贵州茅台2026年第一季度报告摘要", 1780000000000, "3"),
                row("贵州茅台2026年第一季度报告", 1780000000000, "2"),
                row("贵州茅台2026年第一季度报告（英文版）", 1780000000000, "1"),
            ]
        if category == "年报":
            return [row("贵州茅台2025年年度报告", 1770000000000, "4")]
        if category == "三季报":
            return [row("贵州茅台2026年第三季度报告摘要", 1790000000000, "6")]
        return []

    monkeypatch.setattr(akshare_source, "fetch", fake_query)
    env = announcements.periodic_reports("600519", kind="all", page_size=4)

    assert env["ok"] is True
    assert env["partial"] is True
    assert env["errors"] == {"semi": "semi unavailable"}
    assert [item["title"] for item in env["items"]] == [
        "贵州茅台2026年第三季度报告摘要",
        "贵州茅台2026年第一季度报告",
        "贵州茅台2025年年度报告",
    ]
    assert env["excluded_non_full_variants"] == 2

    # 若上游确实只有摘要等变体，仍返回它，避免把“无完整版”伪装成“无报告”。
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *args, **kwargs: [row("贵州茅台2025年年度报告摘要", 1770000000000, "5")],
    )
    fallback = announcements.periodic_reports("600519", kind="annual", page_size=1)
    assert fallback["ok"] is True
    assert fallback["items"][0]["title"].endswith("摘要")


def test_download_rejects_http_200_block_html(tmp_path, monkeypatch):
    from scutio_data import announcements

    block = (
        b"<html><body>Your request originates from an undeclared automated tool"
        + b"x" * 600
        + b"</body></html>"
    )
    monkeypatch.setattr(documents_filings, "download_filing_bytes", lambda *a, **k: block)
    out = announcements.download_announcement_pdf(
        {
            "file_url": "https://www.sec.gov/Archives/example.htm",
            "file_format": "html",
            "market": "us",
            "ticker": "TEST",
        },
        target_dir=str(tmp_path),
        extract_text=False,
    )
    assert out["ok"] is False
    assert not list(tmp_path.glob("*.html"))


def test_single_attempt_pdf_download_does_not_fall_back_to_requests(monkeypatch):
    from types import SimpleNamespace

    import pytest

    monkeypatch.setattr(documents_filings.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(
        documents_filings.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=22,
            stdout=b"",
            stderr=b"upstream rejected request",
        ),
    )
    monkeypatch.setattr(
        documents_filings.http,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("single_attempt must not use requests fallback")
        ),
    )

    with pytest.raises(RuntimeError, match="single filing download attempt failed"):
        documents_filings.download_filing_bytes(
            "https://example.test/report.pdf",
            file_format="pdf",
            timeout=1,
            single_attempt=True,
        )


def test_periodic_reports_us_hk_routing(monkeypatch):
    """港美 periodic_reports 路由到 filings_intl。"""
    from scutio_data import announcements

    monkeypatch.setattr(
        providers_sec,
        "periodic_reports_us",
        lambda code, kind="annual", page_size=20, page_num=1: {
            "ok": True,
            "error": None,
            "source": "periodic_reports_us",
            "items": [{"title": "10-K", "market": "us", "kind": "annual"}],
        },
    )
    monkeypatch.setattr(
        providers_cninfo,
        "periodic_reports_hk",
        lambda code, kind="annual", page_size=20, page_num=1: {
            "ok": True,
            "error": None,
            "source": "periodic_reports_hk",
            "items": [{"title": "年报", "market": "hk", "kind": "annual"}],
        },
    )
    us = announcements.periodic_reports("usAAPL", kind="annual")
    assert us["ok"] is True
    assert us["items"][0]["market"] == "us"
    hk = announcements.periodic_reports("hk00700", kind="annual")
    assert hk["ok"] is True
    assert hk["items"][0]["market"] == "hk"


def test_extract_text_html_and_pdf_bytes():
    """HTML / PDF 文本抽取。"""
    from scutio_data._documents.text import extract_text_from_bytes

    html = b"<html><body><h1>Hello</h1><p>World report</p></body></html>"
    t = extract_text_from_bytes(html, file_format="html")
    assert "Hello" in t and "World" in t
    # minimal pdf may not extract; just ensure no crash on non-pdf
    assert extract_text_from_bytes(b"not-a-pdf", file_format="pdf") == ""


def test_sec_quarter_filters_use_reporting_period_and_fiscal_year(monkeypatch):
    recent = {
        "form": ["10-Q", "10-Q", "10-Q", "10-K", "10-Q"],
        "reportDate": ["2026-06-27", "2026-03-28", "2025-12-27", "2025-09-27", ""],
        "filingDate": ["2026-07-31", "2026-05-01", "2026-01-30", "2025-10-31", "2025-01-30"],
        "accessionNumber": ["a", "b", "c", "d", "e"],
        "primaryDocument": ["q3.htm", "q2.htm", "q1.htm", "annual.htm", "unknown.htm"],
    }
    monkeypatch.setattr(
        providers_sec,
        "load_submissions",
        lambda code: ("AAPL", "0000320193", {"filings": {"recent": recent}}),
    )
    for kind, expected in (("q1", "2025-12-27"), ("semi", "2026-03-28"), ("q3", "2026-06-27")):
        out = providers_sec.periodic_reports_us("usAAPL", kind=kind)
        assert [row["report_date"] for row in out["items"]] == [expected]
        assert out["items"][0]["kind"] == kind
        assert out["items"][0]["kind_basis"] == "annual_report_date_interval"
        assert out["partial"] and out["unclassified_quarters"] == 1
    out = providers_sec.periodic_reports_us("usAAPL", kind="all")
    assert [row["kind"] for row in out["items"]] == ["q3", "semi", "q1", "annual", "quarter"]
    assert providers_sec._quarter_kind("2026-03-31", [date(2025, 12, 31)]) == "q1"


def test_pdf_unreadable_font_mapping_is_reported_and_cached_text_invalidated(tmp_path, monkeypatch):
    """非空乱码不能作为可检索的财报正文，保留原文并给出明确错误。"""
    from types import SimpleNamespace

    import pypdf
    from scutio_data._documents import text as document_text
    from scutio_data.paths import write_text_sidecar

    garbled = "中文" + "ɚཧɚʞϋ " * 100
    monkeypatch.setattr(
        pypdf,
        "PdfReader",
        lambda _: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: garbled)]),
    )
    monkeypatch.setattr(document_text.shutil, "which", lambda _: None)
    path = tmp_path / "annual.pdf"
    path.write_bytes(b"%PDF-original-preserved")
    path.with_suffix(".txt").write_text(garbled, encoding="utf-8")
    text_path, error = write_text_sidecar(path)
    assert error.startswith("suspect_pdf_text_encoding:")
    assert "text extraction unreadable" in Path(text_path).read_text(encoding="utf-8")
    assert garbled not in Path(text_path).read_text(encoding="utf-8")
    assert path.read_bytes() == b"%PDF-original-preserved"
    with pytest.raises(ValueError, match="suspect_pdf_text_encoding"):
        document_text.extract_text_from_path(path)


def test_pdf_encoding_fallback_accepts_readable_text(monkeypatch):
    from types import SimpleNamespace

    import pypdf
    from scutio_data._documents import text as document_text

    monkeypatch.setattr(
        pypdf,
        "PdfReader",
        lambda _: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "ɚཧɚʞϋ " * 100)]),
    )
    monkeypatch.setattr(document_text.shutil, "which", lambda _: "/fake/pdftotext")
    expected = "腾讯控股年度报告 Revenue 751,766 million RMB"
    monkeypatch.setattr(
        document_text.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=expected.encode()),
    )
    assert document_text.extract_text_from_bytes(b"%PDF", file_format="pdf") == expected
    assert not document_text._suspect_pdf_encoding("中文财务报表 " * 100)
    assert not document_text._suspect_pdf_encoding("Revenue cash flow " * 100)


def test_default_cache_dirs_by_code(tmp_path, monkeypatch):
    """披露/研报默认按个股分子目录。"""
    from scutio_data import research

    monkeypatch.setenv("SCUTIO_HOME", str(tmp_path))
    # reload path root via functions that call default_scutio_home
    fdir = documents_filings._default_filings_dir("a", "600519")
    assert fdir.as_posix().endswith("cache/documents/filings/a/600519")
    fdir_us = documents_filings._default_filings_dir("us", "AAPL")
    assert fdir_us.as_posix().endswith("cache/documents/filings/us/AAPL")
    rdir = research.default_reports_dir("600519")
    assert rdir.as_posix().endswith("cache/documents/reports/600519")
    assert (
        research.default_reports_dir("_misc").as_posix().endswith("cache/documents/reports/_misc")
    )
    assert documents_report_paths._report_code_from_record({"stockCode": "300750"}) == "300750"


def test_financial_report_rejects_unknown_type_and_period_before_fetch(monkeypatch):
    """拼错三表类型/周期必须失败，不能静默调用另一张表。"""
    from scutio_data import fundamentals

    called = []
    monkeypatch.setattr(
        fundamentals,
        "_financial_report_a",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )
    bad_type = fundamentals.financial_report("600519", report_type="cashfow")
    bad_period = fundamentals.financial_report("600519", period="quater")
    bad_num = fundamentals.financial_report("600519", num=0)
    assert bad_type["ok"] is False and "unknown report_type" in bad_type["error"]
    assert bad_period["ok"] is False and "unknown period" in bad_period["error"]
    assert bad_num["ok"] is False and "num must be >= 1" in bad_num["error"]
    assert called == []


def test_security_quote_mixed_invalid_code_is_partial(monkeypatch):
    """批次中的非法代码必须进入 invalid/missing 并降低完整性。"""
    from scutio_data import market

    monkeypatch.setattr(
        quote_source,
        "tencent_quote",
        lambda _codes: {
            "sh600519": {
                "symbol": "sh600519",
                "code": "600519",
                "price": 100,
                "source": "tencent",
            }
        },
    )
    out = market.security_quote(["600519", "BAD"], sources=("tencent",))
    assert out["ok"] is True
    assert out["partial"] is True
    assert out["error"] is None and out["warning"]
    assert out["source"] == "tencent"
    assert out["invalid"] == ["BAD"]
    assert "BAD" in out["missing"]
    assert out["requested_count"] == 2
    assert out["returned_count"] == 1


def test_security_quote_omits_ambiguous_bare_alias(monkeypatch):
    from scutio_data import market

    monkeypatch.setattr(
        quote_source,
        "tencent_quote",
        lambda _codes: {
            "sh000001": {"code": "000001", "price": 3000, "last_close": 2990},
            "sz000001": {"code": "000001", "price": 10, "last_close": 9.9},
        },
    )
    out = market.security_quote(["sh000001", "sz000001"], sources=("tencent",))
    assert out["ok"] is True
    assert out["quotes"]["sh000001"]["price"] == 3000
    assert out["quotes"]["sz000001"]["price"] == 10
    assert "000001" not in out["quotes"]


def test_a_only_facades_reject_hk_us_without_network(monkeypatch):
    """A 股专属门面应在发请求前返回稳定 unsupported_market。"""
    from scutio_data import announcements, capital, research

    def forbidden(*_args, **_kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(providers_eastmoney, "em_get", forbidden)
    from scutio_data._providers.akshare import client as akshare_source

    monkeypatch.setattr(akshare_source, "fetch", forbidden)
    monkeypatch.setattr(cninfo.http, "post", forbidden)

    cases = [
        capital.concept_blocks("hk00700"),
        capital.stock_fund_flow_120d("hk00700"),
        capital.margin_trading("usAAPL"),
        capital.block_trade("hk00700"),
        capital.holder_num_change("usAAPL"),
        capital.dividend_history("hk00700"),
        research.stock_reports("usAAPL"),
        research.eps_forecast("hk00700"),
        announcements.stock_announcements("usAAPL"),
        announcements.irm("hk00700"),
        announcements.lockup_expiry("usAAPL", "2026-08-05"),
        capital.dragon_tiger_board("usAAPL", "2026-08-05"),
    ]
    assert all(item["ok"] is False for item in cases)
    assert all(item.get("error_code") == "unsupported_market" for item in cases)


def test_public_facades_return_envelopes_for_invalid_codes():
    from scutio_data import capital, fundamentals

    info = fundamentals.stock_info("600519foo")
    actions = capital.corporate_actions("600519foo")
    materials = fundamentals.stock_materials("600519foo")
    assert info["ok"] is False and info["error_code"] == "invalid_code"
    assert actions["ok"] is False and actions["error_code"] == "invalid_code"
    assert materials["ok"] is False
    assert materials["items"] == []


def test_package_root_does_not_export_transport_internals():
    import scutio_data

    assert "EM_SESSION" not in scutio_data.__all__
    assert "em_get" not in scutio_data.__all__
    assert "security_quote" not in scutio_data.__all__
    assert scutio_data.market.security_quote is not None


@pytest.mark.parametrize("source", ["tencent", "sina", "eastmoney", "hithink"])
def test_quote_facade_rejects_conflicting_exchange_per_security(monkeypatch, source):
    from scutio_data import market

    bad = {"symbol": "sz000001", "code": "000001", "exchange": "sh", "price": 3000}
    good = {"symbol": "sh600519", "code": "600519", "exchange": "sh", "price": 1500}

    def fetch(codes):
        return {"sz000001": bad, "sh600519": good}

    if source == "hithink":
        monkeypatch.setattr(market.hithink, "quotes", fetch)
    else:
        monkeypatch.setattr(quote_source, source + "_quote", fetch)
    result = market.security_quote(["sz000001", "sh600519"], sources=[source])
    assert result["ok"] and result["partial"]
    assert result["missing"] == ["sz000001"]
    assert "sz000001" not in result["quotes"]
    assert result["quotes"]["sh600519"]["price"] == 1500
    assert "identity mismatch" in result["errors"][source + ":sz000001"]


def test_quote_identity_failure_retries_only_the_missing_security(monkeypatch):
    from scutio_data import market

    monkeypatch.setattr(
        quote_source,
        "tencent_quote",
        lambda codes: {
            "sz000001": {"symbol": "sz000001", "exchange": "sh", "price": 3000},
            "sh600519": {"symbol": "sh600519", "exchange": "sh", "price": 1500},
        },
    )
    requests = []

    def fallback(codes):
        requests.append(codes)
        return {"sz000001": {"symbol": "sz000001", "exchange": "sz", "price": 11}}

    monkeypatch.setattr(quote_source, "sina_quote", fallback)
    result = market.security_quote(["sz000001", "sh600519"], sources=["tencent", "sina"])
    assert result["ok"] and not result["partial"]
    assert result["missing"] == [] and result["returned_count"] == 2
    assert requests == [["sz000001"]]
    assert result["quotes"]["sz000001"]["price"] == 11
    assert result["quotes"]["sh600519"]["source"] == "tencent"
    assert "identity mismatch" in result["errors"]["tencent:sz000001"]


def test_em_transport_uses_isolated_sessions_and_shared_retry(monkeypatch):
    from scutio_data._runtime.http import Session

    seen = []

    def request(self, method, url, **kwargs):
        seen.append((self, kwargs))
        return response(data={"ok": True})

    monkeypatch.setattr(Session, "request", request)
    providers_eastmoney.em_get("https://push2.eastmoney.com/api/qt/stock/get")
    providers_eastmoney.em_get("https://push2.eastmoney.com/api/qt/stock/get")
    assert seen[0][0] is not seen[1][0]
    assert all(item[1]["_source_attempts"] == 2 for item in seen)
    assert all(item[0].headers.get("User-Agent") for item in seen)


@pytest.mark.parametrize("error", [requests.exceptions.ProxyError, requests.exceptions.SSLError])
def test_em_proxy_recovery_is_owned_by_shared_http(monkeypatch, error):
    from scutio_data._runtime.http import Session

    routes = []

    def transfer(self, method, url, *, network_trust_env, **kwargs):
        routes.append(network_trust_env)
        if network_trust_env:
            raise error("private-proxy")
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {"ok": True})

    monkeypatch.setattr(Session, "_transport_request", transfer)
    assert providers_eastmoney.em_get("https://push2.eastmoney.com/api/qt/stock/get").json()["ok"]
    assert routes == [True, False]


@pytest.mark.parametrize(
    "error", [requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError]
)
def test_em_transient_retry_uses_the_shared_executor(monkeypatch, error):
    from unittest.mock import Mock

    from scutio_data._runtime.http import Session

    call = Mock(side_effect=[error("down"), SimpleNamespace(status_code=200, headers={})])
    monkeypatch.setattr(Session, "_transport_request", call)
    assert (
        providers_eastmoney.em_get("https://push2.eastmoney.com/api/qt/stock/get").status_code
        == 200
    )
    assert call.call_count == 2


def test_em_invalid_url_is_not_retried(monkeypatch):
    from unittest.mock import Mock

    from scutio_data._runtime.http import Session

    call = Mock(side_effect=requests.exceptions.InvalidURL("bad url"))
    monkeypatch.setattr(Session, "_transport_request", call)
    with pytest.raises(requests.exceptions.InvalidURL):
        providers_eastmoney.em_get("https://push2.eastmoney.com/api/qt/stock/get")
    assert call.call_count == 1
