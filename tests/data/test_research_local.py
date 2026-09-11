"""Offline tests for keyless local research replacements."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import scutio_data._providers.eastmoney as providers_eastmoney
import scutio_data.research as research
from scutio_data._documents import reports as report_documents
from scutio_data._providers import eastmoney_research
from scutio_data.research import discovery as research_discovery
from scutio_data.research import local as research_local


def test_local_report_search_expands_theme_and_ranks_matches():
    """Theme synonyms match report text and higher scores rank first."""
    records = [
        {
            "title": "人形机器人伺服减速器与行星滚柱丝杠产业链",
            "publishDate": "2026-01-02",
            "infoCode": "high",
        },
        {
            "title": "宏观市场周报",
            "publishDate": "2026-01-03",
            "infoCode": "none",
        },
        {
            "title": "行星滚柱丝杠技术跟踪",
            "publishDate": "2026-01-01",
            "infoCode": "screw",
        },
    ]
    env = research.local_report_search(
        "人形机器人 行星滚柱丝杠",
        records=records,
        limit=10,
    )
    assert env["ok"] is True
    results = env["items"]
    assert [item["infoCode"] for item in results] == ["high", "screw"]
    assert "机器人" in results[0]["_matched_terms"]
    assert results[0]["source"] == "eastmoney_local"


def test_local_report_search_fetches_eastmoney_records(monkeypatch):
    """The default path fetches industry reports without credentials."""
    calls = []

    def fake_reports(industry_code, max_pages, begin):
        calls.append((industry_code, max_pages, begin))
        return [{"title": "人工智能算力服务器", "publishDate": "2026-02-01"}]

    monkeypatch.setattr(eastmoney_research, "industry_reports", fake_reports)
    env = research.local_report_search("AI 算力", begin="2026-01-01", max_pages=2)
    assert calls == [("*", 2, "2026-01-01")]
    assert env["ok"] is True
    assert env["items"][0]["_matched_terms"]


def test_local_report_search_mixed_case_terms_score_positive():
    """Matched terms like AI must contribute score even when stored upper-case."""
    records = [
        {
            "title": "AI 大模型应用进展",
            "publishDate": "2026-03-01",
            "infoCode": "ai",
        },
        {
            "title": "无关宏观周报",
            "publishDate": "2026-03-02",
            "infoCode": "none",
        },
    ]
    env = research.local_report_search("AI", records=records, limit=5)
    assert env["ok"] is True
    results = env["items"]
    assert results
    assert results[0]["infoCode"] == "ai"
    assert results[0]["_local_score"] > 0
    assert any(t.lower() == "ai" or "ai" in t.lower() for t in results[0]["_matched_terms"])


def test_local_stock_screen_supports_aliases_and_missing_sort_values():
    """Local numeric filters and Chinese aliases work without remote screening."""
    records = [
        {"code": "000001", "pe_ttm": 12, "mcap_yi": 100, "industry": "银行"},
        {"code": "000002", "pe_ttm": 8, "mcap_yi": 200, "industry": "地产"},
        {"code": "000003", "pe_ttm": None, "mcap_yi": 50, "industry": "银行"},
    ]
    results = research.local_stock_screen(
        records,
        filters={"市盈率": {"max": 15}, "行业": {"contains": "银行"}},
        sort_by="市盈率",
    )
    assert [item["code"] for item in results] == ["000001"]

    bank_results = research.local_stock_screen(
        records,
        filters={"行业": {"contains": "银行"}},
        sort_by="pe_ttm",
    )
    assert [item["code"] for item in bank_results] == ["000001", "000003"]


@pytest.mark.parametrize("alias", ["市值", "总市值"])
@pytest.mark.parametrize("descending", [True, False])
def test_local_stock_screen_market_cap_alias_uses_quote_fields(alias, descending):
    records = [
        {"symbol": "sh600519", "mcap_yi": 17000},
        {"symbol": "sz000001", "mcap_yi": 2100},
        {"symbol": "sz000002", "mcap_yi": 800},
        {"symbol": "sz000003", "mcap_yi": None},
    ]
    expected = records[:2] if descending else list(reversed(records[:2]))
    assert (
        research.local_stock_screen(
            records, filters={alias: {"min": 1000}}, sort_by=alias, descending=descending
        )
        == expected
    )
    sorted_rows = research.local_stock_screen(records, sort_by=alias, descending=descending)
    assert sorted_rows[-1] == records[-1]
    assert sorted_rows[:-1] == (records[:3] if descending else list(reversed(records[:3])))


def test_local_stock_screen_preserves_input_and_supports_limit_none():
    """Screening returns copied records and can return the full filtered set."""
    records = [{"code": "000001", "pb": 1.2}, {"code": "000002", "pb": 2.5}]
    results = research.local_stock_screen(records, filters={"pb": {"min": 1}}, limit=None)
    assert results == records
    assert results[0] is not records[0]


def test_report_detail_url_and_html_extractors():
    """Detail URL templates and HTML extractors stay stable offline."""
    record = {
        "title": "T",
        "infoCode": "AP202601010000000001",
        "encodeUrl": "abc+/=",
        "industryName": "游戏Ⅱ",
    }
    url = research.report_detail_url(record, kind="industry")
    assert "zw_industry.jshtml" in url
    assert "encodeUrl=" in url

    html = """
    <h1>标题A</h1>
    <p>这是一段足够长的研报摘要正文用于测试抽取逻辑是否工作正常。</p>
    <p>免责声明：本报告仅供参考</p>
    <span class="to-link">
      <a class="pdf-link" href="https://pdf.dfcfw.com/pdf/H3_AP202601010000000001_1.pdf?123.pdf">PDF</a>
    </span>
    """
    assert report_documents._extract_pdf_url_from_html(html).startswith(
        "https://pdf.dfcfw.com/pdf/"
    )
    abstract = report_documents._extract_paragraphs(html, max_chars=200)
    assert "足够长的研报摘要" in abstract
    assert "免责声明" not in abstract


def test_download_pdf_prefers_curl_and_validates_magic(monkeypatch, tmp_path):
    """PDF path uses detail URL + curl and rejects non-PDF bodies."""
    record = {
        "title": "示例研报",
        "orgSName": "测试证券",
        "publishDate": "2026-01-02",
        "infoCode": "AP202601020000000002",
        "encodeUrl": "enc",
    }

    monkeypatch.setattr(
        report_documents,
        "report_page_detail",
        lambda *args, **kwargs: {
            "ok": True,
            "pdf_url": "https://pdf.dfcfw.com/pdf/H3_AP202601020000000002_1.pdf?ts.pdf",
            "abstract": "摘要",
            "url": "http://example",
            "title": "示例研报",
            "error": None,
            "kind": "industry",
            "info_code": record["infoCode"],
        },
    )
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        report_documents,
        "_download_bytes_curl",
        lambda url, timeout=60: b"%PDF-1.4 fake-content-for-test",
    )
    monkeypatch.setattr(
        report_documents,
        "_download_bytes_requests",
        lambda url, timeout=60: b"<script>blocked</script>",
    )

    def _sidecar(path, content=b""):
        tp = Path(path).with_suffix(".txt")
        tp.write_text("report body text", encoding="utf-8")
        return str(tp), None

    monkeypatch.setattr(report_documents, "_write_report_text_sidecar", _sidecar)
    out = research.download_pdf(record, target_dir=str(tmp_path))
    assert out["ok"] is True
    assert out.get("path")
    assert Path(out["path"]).read_bytes().startswith(b"%PDF")
    assert out.get("text_path")
    assert Path(out["text_path"]).exists()
    assert "report body" in Path(out["text_path"]).read_text(encoding="utf-8")
    assert out.get("publish_date") == "2026-01-02"
    assert "缓存" in (out.get("note") or "") or "stock_reports" in (out.get("note") or "")


def test_list_local_reports_stale_flag(monkeypatch, tmp_path):
    """本地列表 vs 在线最新日 → stale。"""
    monkeypatch.setenv("SCUTIO_HOME", str(tmp_path))
    code = "600519"
    d = research.default_reports_dir(code)
    d.mkdir(parents=True, exist_ok=True)
    old = d / "2020-01-01_券商_旧研报.pdf"
    old.write_bytes(b"%PDF-1.4 old")
    (d / "2020-01-01_券商_旧研报.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr(
        research_discovery,
        "stock_reports",
        lambda code, max_pages=2, begin="2000-01-01": {
            "ok": True,
            "error": None,
            "source": "stock_reports",
            "symbol": "sh600519",
            "items": [
                {
                    "stockCode": "600519",
                    "title": "新",
                    "publishDate": "2026-06-01",
                    "orgSName": "X",
                },
            ],
        },
    )
    env = research.list_local_reports(code, compare_online=True)
    assert env["ok"] is True
    assert env.get("local_latest") == "2020-01-01"
    assert env.get("online_latest") == "2026-06-01"
    assert env.get("stale") is True
    assert len(env["items"]) == 1
    assert "stock_reports" in (env.get("note") or "")


def test_list_local_reports_reuses_online_records(monkeypatch, tmp_path):
    monkeypatch.setattr(research_local, "default_reports_dir", lambda *_: tmp_path)
    monkeypatch.setattr(
        research_discovery,
        "stock_reports",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must reuse online records")),
    )
    env = research.list_local_reports(
        "600519",
        compare_online=True,
        online_reports={
            "ok": True,
            "symbol": "sh600519",
            "items": [{"stockCode": "600519", "publishDate": "2026-08-01"}],
        },
    )
    assert env["ok"] is True
    assert env["online_latest"] == "2026-08-01"
    assert env["stale"] is True


@pytest.fixture
def local_report_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(research_local, "default_reports_dir", lambda *_: tmp_path)
    monkeypatch.setattr(
        research_discovery,
        "stock_reports",
        lambda *a, **k: pytest.fail("provided reports must not trigger a new request"),
    )
    path = tmp_path / "2026-09-10_券商_研报.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    return path


def _identified_report(**extra):
    return {"stockCode": "600519", "publishDate": "2026-09-01", **extra}


@pytest.mark.parametrize(
    "online",
    [
        {"ok": True, "symbol": "sz000001", "items": [_identified_report(stockCode="000001")]},
        {"ok": True, "code": "000001", "items": [_identified_report()]},
        {"ok": True, "symbol": "sh600519", "code": "000001", "items": [_identified_report()]},
        {"ok": True, "stockCode": "000001", "items": [_identified_report()]},
        {"ok": True, "symbol": "sh600519", "items": [_identified_report(stockCode="000001")]},
        [_identified_report(), _identified_report(stockCode="000001")],
        [_identified_report(symbol="sz000001")],
        [_identified_report(exchange="SZ")],
        {"ok": True, "symbol": "sh600519", "items": [{"publishDate": "2026-09-01"}]},
        {"ok": True, "items": []},
        [],
    ],
)
def test_list_local_reports_rejects_wrong_conflicting_or_missing_identity(
    local_report_cache, online
):
    env = research.list_local_reports("600519", online_reports=online)
    assert env["ok"] is True
    assert env["partial"] is True
    assert "identity" in env["online_error"]
    assert env["online_latest"] is None
    assert env["stale"] is None
    assert [row["path"] for row in env["items"]] == [str(local_report_cache)]


@pytest.mark.parametrize(
    "online",
    [
        {"ok": True, "symbol": "sh600519", "items": [_identified_report()]},
        {"ok": True, "code": "600519", "items": []},
        [_identified_report()],
    ],
)
def test_list_local_reports_complete_identified_result_can_confirm_no_gap(
    local_report_cache, online
):
    env = research.list_local_reports("600519", online_reports=online)
    assert env["ok"] is True
    assert env["partial"] is False
    assert env["stale"] is False
    assert env["online_error"] is None
    assert [row["path"] for row in env["items"]] == [str(local_report_cache)]


@pytest.mark.parametrize(
    "upstream",
    [
        {"partial": True},
        {"stale": True},
        {"coverage": {"truncated": True}},
        {"coverage": {"complete": False}},
        {"coverage": {"pages_fetched": 1, "total_pages": 2}},
        {"coverage": {"total_pages": 2}},
        {"coverage": {"returned_count": 1, "available_count": 5}},
        {"errors": [{"page": 2, "error": "timeout"}]},
        {"ok": False, "error": "first page failed"},
    ],
)
def test_list_local_reports_incomplete_online_never_confirms_no_gap(local_report_cache, upstream):
    online = {"ok": True, "symbol": "sh600519", "items": [_identified_report()], **upstream}
    env = research.list_local_reports("600519", online_reports=online)
    assert env["ok"] is True
    assert env["partial"] is True
    assert env["stale"] is None
    assert env["online_error"]
    assert [row["path"] for row in env["items"]] == [str(local_report_cache)]
    for key in ("coverage", "errors"):
        if key in upstream:
            assert env["online_" + key] == upstream[key]


def test_list_local_reports_partial_can_still_prove_a_newer_report_exists(local_report_cache):
    env = research.list_local_reports(
        "600519",
        online_reports={
            "ok": True,
            "symbol": "sh600519",
            "items": [_identified_report(publishDate="2026-09-11")],
            "partial": True,
        },
    )
    assert env["partial"] is True
    assert env["stale"] is True
    assert env["online_error"]
    assert env["online_latest"] == "2026-09-11"


@pytest.mark.parametrize("published", [None, "2026-99-99", "unknown"])
def test_list_local_reports_unknown_dates_cannot_confirm_no_gap(local_report_cache, published):
    env = research.list_local_reports(
        "600519", online_reports=[_identified_report(publishDate=published)]
    )
    assert env["partial"] is True
    assert env["stale"] is None
    assert "publication date" in env["online_error"]


def test_list_local_reports_fetch_failure_keeps_local_files(local_report_cache, monkeypatch):
    def fail_fetch(*args, **kwargs):
        raise RuntimeError("request deadline exceeded")

    monkeypatch.setattr(research_discovery, "stock_reports", fail_fetch)
    env = research.list_local_reports("600519")
    assert env["ok"] is True
    assert env["partial"] is True
    assert env["stale"] is None
    assert env["online_error"] == "request deadline exceeded"
    assert [row["path"] for row in env["items"]] == [str(local_report_cache)]


def test_list_local_reports_local_only_does_not_consume_online_input(local_report_cache):
    env = research.list_local_reports("600519", compare_online=False, online_reports=object())
    assert env["ok"] is True
    assert env["partial"] is False
    assert env["stale"] is None
    assert env["online_error"] is None
    assert [row["path"] for row in env["items"]] == [str(local_report_cache)]


@pytest.mark.parametrize("compare_online", [False, True])
@pytest.mark.parametrize("code", ["sh000001", "000001.SH", "sz600519", "sh510300", "hk00700"])
def test_list_local_reports_guards_company_identity_before_reading_cache(
    monkeypatch, code, compare_online
):
    monkeypatch.setattr(
        research_local,
        "default_reports_dir",
        lambda *_: pytest.fail("invalid company identity must not reach a cache directory"),
    )
    monkeypatch.setattr(
        research_discovery,
        "stock_reports",
        lambda *a, **k: pytest.fail("invalid company identity must not reach a provider"),
    )
    env = research.list_local_reports(code, compare_online=compare_online)
    assert env["ok"] is False
    assert env["error"].startswith(("unsupported_asset:", "unsupported_market:"))
    assert env["items"] == []


def test_list_local_reports_local_only_preserves_valid_bank_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("SCUTIO_HOME", str(tmp_path))
    directory = research.default_reports_dir("000001")
    directory.mkdir(parents=True)
    path = directory / "2026-09-10_平安银行.pdf"
    path.write_bytes(b"%PDF-1.4 fixture")
    for code in ("000001", "sz000001", "000001.SZ"):
        env = research.list_local_reports(code, compare_online=False)
        assert env["ok"] is True
        assert [row["path"] for row in env["items"]] == [str(path)]
    rejected = research.list_local_reports("sh000001", compare_online=False)
    assert rejected["ok"] is False
    assert rejected["items"] == []


def test_report_page_detail_parses_mock_html(monkeypatch):
    """report_page_detail maps HTML into abstract and pdf_url."""
    from types import SimpleNamespace

    html = (
        "<h1>行业周报</h1>"
        "<p>游戏版号发放节奏稳定，关注新品周期与暑期档表现相关论述需要足够长度。</p>"
        '<a class="pdf-link" href="https://pdf.dfcfw.com/pdf/H3_X_1.pdf?1.pdf"></a>'
    )
    monkeypatch.setattr(
        providers_eastmoney,
        "em_get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, text=html),
    )
    detail = research.report_page_detail(
        {"title": "t", "infoCode": "APX", "encodeUrl": "e", "industryName": "游戏Ⅱ"}
    )
    assert detail["ok"] is True
    assert "游戏版号" in detail["abstract"]
    assert detail["pdf_url"].startswith("https://pdf.dfcfw.com/")
    assert detail["title"] == "行业周报"


def test_report_dedup_preserves_distinct_periods_and_document_ids():
    records = [
        {"title": "银行周报", "publishDate": "2026-08-01", "infoCode": "AP1"},
        {"title": "银行周报", "publishDate": "2026-08-08", "infoCode": "AP2"},
        {"title": "银行周报", "publishDate": "2026-08-08", "infoCode": "AP2"},
    ]
    result = research.local_report_search("银行", records=records)
    assert [row["infoCode"] for row in result["items"]] == ["AP2", "AP1"]
    assert result["search_coverage"]["matched"] == 2
    without_ids = [{k: v for k, v in row.items() if k != "infoCode"} for row in records]
    assert len(research.dedup_articles(without_ids)) == 2


def test_direct_screen_excludes_nonfinite_values_like_cli():
    records = [
        {"code": str(i), "pe_ttm": value}
        for i, value in enumerate(["NaN", float("nan"), "Infinity", "-Infinity", True, None, "10"])
    ]
    assert research.local_stock_screen(records, filters={"pe": {"min": 5, "max": 15}}) == [
        records[-1]
    ]
