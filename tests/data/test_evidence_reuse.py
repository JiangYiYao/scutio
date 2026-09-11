"""跨模块复用必须保持证券身份、预测年份、源时点和覆盖缺口。"""

from pathlib import Path

import pytest
from scutio_data import research, valuation
from scutio_data._documents import filings
from scutio_data._documents import reports as documents
from scutio_data._providers.akshare import client
from scutio_data._providers.hithink import client as hithink

OLD = "2020-01-01T00:00:00+00:00"
NEW = "2026-09-10T00:00:00+00:00"


def quote_env(symbol="sz000001", **extra):
    return {
        "ok": True,
        "retrieved_at": OLD,
        "quotes": {
            symbol: {
                "symbol": symbol,
                "code": symbol[2:],
                "price": 10,
                "pe_ttm": 5,
                "pb": 1,
                "source": "tencent",
                "retrieved_at": OLD,
                "data_as_of": OLD,
                **extra,
            }
        },
    }


@pytest.mark.parametrize(
    "requested,supplied",
    [("sh000001", "sz000001"), ("hk00001", "sz000001"), ("600519", "sz000001")],
)
def test_valuation_rejects_cross_asset_reuse(requested, supplied):
    env = valuation.valuation_snapshot(requested, quote_env=quote_env(supplied), sources=["quote"])
    assert not env["ok"]
    assert env.get("price") is None


def test_valuation_rejects_conflicting_row_under_correct_key():
    env = quote_env()
    env["quotes"] = {"sh000001": env["quotes"]["sz000001"]}
    assert not valuation.valuation_snapshot("sh000001", quote_env=env, sources=["quote"])["ok"]


def test_pure_quote_key_requires_exchange_identity():
    env = quote_env()
    row = env["quotes"]["sz000001"]
    env["quotes"] = {"000001": row}
    assert valuation.valuation_snapshot("sz000001", quote_env=env, sources=["quote"])["ok"]
    del row["symbol"]
    assert not valuation.valuation_snapshot("sz000001", quote_env=env, sources=["quote"])["ok"]
    row["exchange"] = "sz"
    assert valuation.valuation_snapshot("sz000001", quote_env=env, sources=["quote"])["ok"]


def test_valuation_keeps_reused_quote_time_and_partial():
    env = valuation.valuation_snapshot(
        "000001", quote_env=quote_env(partial=True), sources=["quote"]
    )
    assert env["retrieved_at"] == env["data_as_of"] == env["input_quote_retrieved_at"] == OLD
    assert env["computed_at"] != OLD and env["partial"]
    assert env["field_timestamps"]["price"]["data_as_of"] == OLD


def test_free_metric_refresh_preserves_old_price_time(monkeypatch):
    monkeypatch.setattr(
        valuation, "security_quote", lambda *a, **k: quote_env(retrieved_at=NEW, data_as_of=NEW)
    )
    env = valuation.valuation_snapshot(
        "000001", quote_env=quote_env(pe_ttm=None, pb=None), sources=["quote"]
    )
    assert env["field_timestamps"]["price"]["retrieved_at"] == OLD
    assert env["field_timestamps"]["pe_ttm"]["retrieved_at"] == NEW
    assert env["retrieved_at"] == OLD


def test_paid_metrics_keep_separate_input_quote_time(monkeypatch):
    monkeypatch.setattr(
        hithink,
        "valuation",
        lambda code: {
            "source": "hithink",
            "pe_ttm": 8,
            "pb": 2,
            "retrieved_at": NEW,
            "data_as_of": NEW,
        },
    )
    env = valuation.valuation_snapshot("000001", quote_env=quote_env(), sources=["hithink"])
    assert env["retrieved_at"] == NEW
    assert env["input_quote_retrieved_at"] == OLD
    assert env["field_timestamps"]["price"]["data_as_of"] == OLD
    assert env["field_timestamps"]["pe_ttm"]["data_as_of"] == NEW


def report(**extra):
    return {
        "stockCode": "600519",
        "publishDate": "2026-01-01",
        "orgSName": "broker",
        "forecast_years": [2026],
        "predictThisYearEps": 1.5,
        "infoCode": "new",
        **extra,
    }


@pytest.mark.parametrize(
    "rows",
    [
        [report(stockCode="000001")],
        [report(), report(stockCode="000001")],
        [{"forecast_years": [2026]}],
    ],
)
def test_revision_rejects_wrong_mixed_or_unknown_stock(rows):
    assert not research.consensus_revisions("600519", reports=rows)["ok"]


def test_revision_rejects_wrong_envelope_identity():
    assert not research.consensus_revisions(
        "600519", reports={"ok": True, "code": "000001", "items": [report()]}
    )["ok"]


def test_revision_aligns_same_fiscal_year_across_calendar_years():
    env = research.consensus_revisions(
        "600519",
        reports=[
            report(
                publishDate="2025-12-01",
                forecast_years=[2025, 2026],
                predictThisYearEps=1,
                predictNextYearEps=1.5,
                infoCode="old",
            ),
            report(),
        ],
    )
    row = next(row for row in env["items"] if row["date"] == "2026-01-01")
    assert row["fiscal_year"] == 2026 and row["eps"] == row["previous_eps"] == 1.5
    assert row["change"] == 0 and row["direction"] == "flat"
    assert row["previous_info_code"] == "old" and row["info_code"] == "new"


def test_revision_keeps_upstream_gaps_and_skips_unknown_year():
    coverage = {"truncated": True, "available_count": 51, "returned_count": 50}
    errors = [{"page": 2, "error": "timeout"}]
    env = research.consensus_revisions(
        "600519",
        reports={
            "ok": True,
            "partial": True,
            "coverage": coverage,
            "errors": errors,
            "items": [report(forecast_years=None)],
        },
    )
    assert env["ok"] and env["partial"] and env["items"] == []
    assert env["coverage"] == coverage and env["errors"] == errors
    assert env["skipped_reports"][0]["reason"] == "forecast_years_missing_or_invalid"


def test_revision_keeps_failed_upstream_metadata():
    env = research.consensus_revisions(
        "600519",
        reports={
            "ok": False,
            "error": "timeout",
            "errors": [{"page": 1}],
            "coverage": {"complete": False},
        },
    )
    assert not env["ok"] and env["errors"] == [{"page": 1}]
    assert env["coverage"] == {"complete": False}


def raw_report(index):
    return {
        "股票代码": "600519",
        "日期": "2026-09-01",
        "报告名称": str(index),
        "报告PDF链接": f"https://pdf.dfcfw.com/pdf/H3_AP{index}_1.pdf",
    }


def test_stock_report_local_limit_discloses_truncation(monkeypatch):
    monkeypatch.setattr(client, "fetch", lambda *a, **k: [raw_report(n) for n in range(51)])
    env = research.stock_reports("600519", max_pages=1)
    assert env["partial"] and len(env["items"]) == 50
    assert env["coverage"]["available_count"] == 51 and env["coverage"]["truncated"]
    assert env["coverage"]["limit_scope"] == "local_rows"


def test_stock_report_download_reuses_discovery_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "fetch", lambda *a, **k: [raw_report(1)])
    monkeypatch.setattr(
        documents, "report_page_detail", lambda *a, **k: pytest.fail("must reuse PDF URL")
    )
    urls = []

    def download(url):
        urls.append(url)
        return b"%PDF-1.4 test"

    monkeypatch.setattr(documents, "_download_bytes_curl", download)
    row = research.stock_reports("600519")["items"][0]
    env = research.download_pdf(row, target_dir=tmp_path, pause_sec=0, extract_text=False)
    assert env["ok"] and urls == [row["pdf_url"]]
    # Existing caller records still use this spelling.
    candidates, _ = documents._pdf_candidate_urls({"infoCode": "AP1", "pdfUrl": row["pdfUrl"]})
    assert candidates == [row["pdfUrl"]]


@pytest.mark.parametrize("identity_change", ["id", "url", "id_and_url"])
def test_report_cache_distinguishes_documents_with_same_long_title(
    monkeypatch, tmp_path, identity_change
):
    title = "同名研报" * 100
    first = report(title=title, infoCode="AP1", pdf_url="https://example.org/a.pdf")
    second = {**first}
    if "id" in identity_change:
        second["infoCode"] = "AP2"
    if "url" in identity_change:
        second["pdf_url"] = "https://example.org/b.pdf"
    calls = []

    def download(url):
        calls.append(url)
        return b"%PDF-1.7 " + str(len(calls)).encode() * 1200

    monkeypatch.setattr(documents, "_download_bytes_curl", download)

    def fetch(record):
        return research.download_pdf(record, target_dir=tmp_path, pause_sec=0, extract_text=False)

    a, b = fetch(first), fetch(second)
    assert a["ok"] and b["ok"] and a["path"] != b["path"]
    assert Path(a["path"]).read_bytes() != Path(b["path"]).read_bytes()
    assert len(Path(a["path"]).name.encode("utf-8")) < 255
    assert len(Path(b["path"]).name.encode("utf-8")) < 255
    assert fetch(first)["cached"] and fetch(second)["cached"]
    assert len(calls) == 2


def test_report_download_preserves_unidentified_existing_original(monkeypatch, tmp_path):
    legacy = tmp_path / "2026-01-01_broker_same_title.pdf"
    legacy.write_bytes(b"%PDF-1.7 " + b"old" * 500)
    content = b"%PDF-1.7 " + b"new" * 500
    monkeypatch.setattr(documents, "_download_bytes_curl", lambda url: content)
    row = report(title="same title", infoCode="AP1", pdf_url="https://example.org/a.pdf")
    env = research.download_pdf(row, target_dir=tmp_path, pause_sec=0, extract_text=False)
    assert env["ok"] and not env["cached"]
    assert Path(env["path"]).read_bytes() == content
    assert legacy.read_bytes() == b"%PDF-1.7 " + b"old" * 500


def test_report_cache_respects_supplied_detail_link(monkeypatch, tmp_path):
    calls = []

    def download(url):
        calls.append(url)
        return b"%PDF-1.7 " + url.encode() * 60

    monkeypatch.setattr(documents, "_download_bytes_curl", download)

    def fetch(url):
        return research.download_pdf(
            report(title="same title"),
            detail={"pdf_url": url},
            target_dir=tmp_path,
            pause_sec=0,
            extract_text=False,
        )

    a, b = fetch("https://example.org/a.pdf"), fetch("https://example.org/b.pdf")
    assert a["ok"] and b["ok"] and a["path"] != b["path"]
    assert Path(a["path"]).read_bytes() != Path(b["path"]).read_bytes()
    assert fetch("https://example.org/b.pdf")["cached"]
    assert calls == ["https://example.org/a.pdf", "https://example.org/b.pdf"]


def test_report_cache_respects_changed_detail_page(monkeypatch, tmp_path):
    monkeypatch.setattr(
        documents,
        "report_page_detail",
        lambda record, **kwargs: {"pdf_url": record["encodeUrl"] + ".pdf"},
    )
    calls = []

    def download(url):
        calls.append(url)
        return b"%PDF-1.7 " + url.encode() * 60

    monkeypatch.setattr(documents, "_download_bytes_curl", download)

    def fetch(url):
        return research.download_pdf(
            report(title="same title", encodeUrl=url),
            target_dir=tmp_path,
            pause_sec=0,
            extract_text=False,
        )

    a, b = fetch("https://example.org/a"), fetch("https://example.org/b")
    assert a["ok"] and b["ok"] and a["path"] != b["path"]
    assert Path(a["path"]).read_bytes() != Path(b["path"]).read_bytes()
    assert fetch("https://example.org/b")["cached"]
    assert calls == ["https://example.org/a.pdf", "https://example.org/b.pdf"]


@pytest.mark.parametrize("input_kind", ["direct_url", "record", "custom_filename"])
def test_filing_cache_binds_original_and_text_to_document_identity(
    monkeypatch, tmp_path, input_kind
):
    calls = []

    def download(url, **kwargs):
        calls.append(url)
        return b"%PDF-1.7 " + url.encode() * 60

    monkeypatch.setattr(filings, "download_filing_bytes", download)

    def write_text(path, *, content, **kwargs):
        target = path.with_suffix(".txt")
        target.write_bytes(content)
        return str(target), None

    monkeypatch.setattr(filings, "_write_sidecar_text", write_text)
    options = {"filename": "annual.pdf"} if input_kind == "custom_filename" else {}

    def item(index):
        url = f"https://example.org/{index}.pdf"
        if input_kind == "direct_url":
            return url
        return {
            "market": "a",
            "sec_code": "600519",
            "date": "2026-01-01",
            "title": "同名公告" * 100,
            "announcement_id": str(index),
            "file_url": url,
        }

    def fetch(index):
        return filings.download_announcement_pdf(item(index), target_dir=tmp_path, **options)

    a, b = fetch(1), fetch(2)
    assert a["ok"] and b["ok"] and a["path"] != b["path"]
    assert a["text_path"] != b["text_path"]
    assert Path(a["path"]).read_bytes() == Path(a["text_path"]).read_bytes()
    assert Path(b["path"]).read_bytes() == Path(b["text_path"]).read_bytes()
    assert Path(a["path"]).read_bytes() != Path(b["path"]).read_bytes()
    assert len(Path(a["path"]).name.encode("utf-8")) < 255
    assert fetch(1)["cached"] and fetch(2)["cached"]
    assert len(calls) == 2


def test_text_only_filings_preserve_distinct_content(tmp_path):
    row = {
        "market": "hk",
        "sec_code": "00700",
        "date": "2026-01-01",
        "announcement_id": "a",
        "title": "同名公告",
        "notice_content": "原公告内容",
    }
    a = filings.download_announcement_pdf(row, target_dir=tmp_path)
    b = filings.download_announcement_pdf(
        {**row, "notice_content": "更正公告内容"}, target_dir=tmp_path
    )
    assert a["ok"] and b["ok"] and a["text_path"] != b["text_path"]
    assert Path(a["text_path"]).read_text() == "原公告内容"
    assert Path(b["text_path"]).read_text() == "更正公告内容"


def test_filing_notice_update_preserves_previous_sidecar(monkeypatch, tmp_path):
    monkeypatch.setattr(
        filings, "download_filing_bytes", lambda *a, **k: b"%PDF-1.7 " + b"body" * 500
    )
    row = {
        "market": "hk",
        "sec_code": "00700",
        "announcement_id": "a",
        "title": "same title",
        "file_url": "https://example.org/a.pdf",
        "notice_content": "原公告内容",
    }
    a = filings.download_announcement_pdf(row, target_dir=tmp_path)
    b = filings.download_announcement_pdf(
        {**row, "notice_content": "更正公告内容"}, target_dir=tmp_path
    )
    assert a["ok"] and b["ok"] and a["text_path"] != b["text_path"]
    assert Path(a["text_path"]).read_text() == "原公告内容"
    assert Path(b["text_path"]).read_text() == "更正公告内容"


def test_revision_does_not_choose_arbitrary_same_day_baseline():
    env = research.consensus_revisions(
        "600519",
        reports=[
            report(publishDate="2026-01-01", infoCode="a", predictThisYearEps=1),
            report(publishDate="2026-01-01", infoCode="b", predictThisYearEps=2),
            report(publishDate="2026-01-02", infoCode="c", predictThisYearEps=1.5),
        ],
    )
    row = env["items"][0]
    assert env["partial"] and row["change"] is None and row["direction"] is None
    assert row["comparison_reason"] == "ambiguous_previous_day"


def test_discovery_forecasts_can_be_reused_for_revision_calculation(monkeypatch):
    monkeypatch.setattr(
        client,
        "fetch",
        lambda *a, **k: [
            {**raw_report(1), "日期": "2026-08-01", "机构": "broker", "2026-盈利预测-收益": 1},
            {**raw_report(2), "机构": "broker", "2026-盈利预测-收益": 1.5},
        ],
    )
    reports = research.stock_reports("600519")
    assert reports["symbol"] == "sh600519"
    assert all(row["symbol"] == "sh600519" for row in reports["items"])
    env = research.consensus_revisions("600519", reports=reports)
    assert env["ok"] and env["items"][0]["change"] == 0.5
    assert env["items"][0]["fiscal_year"] == 2026


def test_stock_report_index_code_cannot_be_relabelled_as_stock(monkeypatch):
    monkeypatch.setattr(
        client, "fetch", lambda *a, **k: pytest.fail("reject identity before request")
    )
    assert not research.stock_reports("sh000001")["ok"]
