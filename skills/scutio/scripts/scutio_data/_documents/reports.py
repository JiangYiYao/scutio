"""研报详情、PDF 下载及文本旁路文件。"""

import re
import shutil
import subprocess
import time
from html import unescape
from pathlib import Path
from urllib.parse import quote

from scutio_data._documents.report_paths import (
    REPORT_CACHE_NOTE,
    _parse_date_from_filename,
    _report_code_from_record,
    default_reports_dir,
)
from scutio_data._providers import eastmoney
from scutio_data._runtime.environment import UA
from scutio_data._runtime.results import result_err, result_ok
from scutio_data._runtime.timeouts import operation, remaining, source
from scutio_data.paths import atomic_write_bytes, document_cache_filename, write_text_sidecar

_PDF_HREF_RE = re.compile(
    r'https?://pdf\.dfcfw\.com/pdf/[^"\'\s<>]+',
    re.IGNORECASE,
)


_PDF_LINK_CLASS_RE = re.compile(
    r'class="pdf-link"[^>]*href="([^"]+)"|href="([^"]+)"[^>]*class="pdf-link"',
    re.IGNORECASE,
)


def _infer_report_kind(record):
    """推断研报详情页模板用的 kind。"""
    if record.get("_report_kind"):
        return record["_report_kind"]
    column = str(record.get("column") or "")
    if column.startswith("002001001"):
        return "macro"
    if column.startswith("002001002"):
        return "strategy"
    if column.startswith("002003001"):
        return "morning"
    # Stock fields present → stock detail page.
    if record.get("stockCode") or record.get("stockName"):
        code = str(record.get("stockCode") or "")
        if code and code not in ("", "0", "None"):
            return "stock"
    # Industry name without stock → industry.
    if record.get("industryName") or record.get("industryCode"):
        return "industry"
    return "industry"


def report_detail_url(record, kind=None):
    """构造东财研报详情 HTML URL。"""
    kind = kind or _infer_report_kind(record)
    encode_url = record.get("encodeUrl") or ""
    info_code = record.get("infoCode") or record.get("info_code") or ""

    if encode_url:
        template = _DETAIL_URL_TEMPLATES.get(kind) or _DETAIL_URL_TEMPLATES["industry"]
        url = template % quote(str(encode_url), safe="")
        return url

    if info_code:
        # infocode form works for many industry/stock pages.
        if kind == "stock":
            return "https://data.eastmoney.com/report/zw_stock.jshtml?infocode=%s" % info_code
        if kind in ("strategy", "macro", "morning"):
            return "https://data.eastmoney.com/report/zw_macresearch.jshtml?infocode=%s" % info_code
        return "https://data.eastmoney.com/report/zw_industry.jshtml?infocode=%s" % info_code
    return None


def _extract_pdf_url_from_html(html_text):
    """从详情页 HTML 提取首个 PDF 链接。"""
    if not html_text:
        return None
    for match in _PDF_LINK_CLASS_RE.finditer(html_text):
        href = match.group(1) or match.group(2)
        if href and "pdf.dfcfw.com" in href:
            return href.replace("&amp;", "&")
    found = _PDF_HREF_RE.findall(html_text)
    if found:
        return found[0].replace("&amp;", "&")
    return None


def _extract_paragraphs(html_text, max_chars=800):
    """拼接 <p> 文本作为粗摘要。"""
    if not html_text:
        return ""
    parts = []
    for block in re.findall(r"<p[^>]*>(.*?)</p>", html_text, flags=re.I | re.S):
        text = re.sub(r"<[^>]+>", "", block)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 20:
            continue
        # Skip boilerplate chrome.
        if any(skip in text for skip in ("免责声明", "点击下载", "返回顶部", "东方财富网")):
            continue
        parts.append(text)
    joined = "\n".join(parts)
    if len(joined) > max_chars:
        return joined[:max_chars].rstrip() + "…"
    return joined


@operation("history")
def report_page_detail(record, kind=None, abstract_chars=800):
    """抓取研报详情页，返回 abstract / pdf_url 等字段的信封 dict。"""
    kind = kind or _infer_report_kind(record)
    info_code = record.get("infoCode") or record.get("info_code") or ""
    detail_url = report_detail_url(record, kind=kind)
    out = {
        "ok": False,
        "error": None,
        "url": detail_url,
        "title": record.get("title") or "",
        "abstract": "",
        "pdf_url": None,
        "kind": kind,
        "info_code": info_code,
    }
    if not detail_url:
        out["error"] = "missing encodeUrl/infoCode"
        return out
    try:
        response = eastmoney.em_get(
            detail_url,
            headers={"Referer": "https://data.eastmoney.com/", "User-Agent": UA},
            timeout=20,
        )
        html_text = response.text or ""
        if response.status_code != 200 or not html_text:
            out["error"] = "detail page HTTP %s" % response.status_code
            return out
        out["pdf_url"] = _extract_pdf_url_from_html(html_text)
        out["abstract"] = _extract_paragraphs(html_text, max_chars=abstract_chars)
        # Prefer h1 if present.
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, flags=re.I | re.S)
        if h1:
            title = re.sub(r"<[^>]+>", "", h1.group(1))
            title = unescape(re.sub(r"\s+", " ", title)).strip()
            if title:
                out["title"] = title
        out["ok"] = True
        if not out["abstract"] and not out["pdf_url"]:
            out["error"] = "no abstract or pdf link on page"
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out


def report_abstract(record, kind=None, max_chars=800):
    """仅返回摘要文本；失败为空串。"""
    detail = report_page_detail(record, kind=kind, abstract_chars=max_chars)
    return detail.get("abstract") or ""


def _pdf_candidate_urls(record, kind=None, detail=None):
    """待尝试的 PDF URL 列表（有序）。"""
    urls = []
    info_code = record.get("infoCode") or record.get("info_code") or ""
    supplied_url = record.get("pdf_url") or record.get("pdfUrl") or record.get("file_url")
    if supplied_url and str(supplied_url).lower().split("?", 1)[0].endswith(".pdf"):
        urls.append(supplied_url)
    # 已有详情时复用；record 已带 PDF 时无需再抓详情页。
    if detail is None and not urls:
        detail = report_page_detail(record, kind=kind)
    detail = detail or {}
    if detail.get("pdf_url"):
        urls.append(detail["pdf_url"])
    # 2) Canonical H3 pattern (works when not blocked).
    if info_code:
        urls.append("https://pdf.dfcfw.com/pdf/H3_%s_1.pdf" % info_code)
    # Dedupe preserve order.
    seen = set()
    ordered = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered, detail


@source("download")
def _download_bytes_curl(url, timeout=180):
    """用系统 curl 下载（常比 requests 少被东财 PDF 风控）。"""
    curl = shutil.which("curl")
    if not curl:
        return None
    timeout = remaining("download", maximum=timeout)
    cmd = [
        curl,
        "-fsSL",
        "--max-time",
        str(timeout),
        "--connect-timeout",
        str(min(5, timeout)),
        "--speed-time",
        "30",
        "--speed-limit",
        "1",
        "-A",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-e",
        "https://data.eastmoney.com/",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode == 0 and result.stdout.startswith(b"%PDF"):
            return result.stdout
    except Exception:
        return None
    return None


@source("download")
def _download_bytes_requests(url, timeout=180):
    """用 em_get/requests 下载；非 PDF 体拒绝。"""
    try:
        response = eastmoney.em_get(
            url,
            headers={
                "User-Agent": UA,
                "Referer": "https://data.eastmoney.com/",
            },
            timeout=timeout,
            download=True,
        )
        content = response.content or b""
        if response.status_code == 200 and content.startswith(b"%PDF"):
            return content
    except Exception:
        return None
    return None


def _write_report_text_sidecar(pdf_path: Path, content: bytes = b"") -> tuple:
    """PDF 旁路 ``.txt``（委托 ``paths.write_text_sidecar``；保留名供单测 monkeypatch）。"""
    return write_text_sidecar(pdf_path, content=content, file_format="pdf")


def _publish_date_from_record(record: dict) -> str:
    record = record or {}
    for key in ("publishDate", "publish_date", "date", "pubDate"):
        raw = record.get(key)
        if raw:
            return str(raw)[:10]
    return ""


@operation("download")
def download_pdf(
    record,
    target_dir=None,
    kind=None,
    pause_sec=0.5,
    extract_text=True,
    detail=None,
):
    """下载研报 PDF 到 ``target_dir``，默认再写同名 ``.txt``。

    默认 ``$SCUTIO_HOME/cache/documents/reports/{code}/``（无个股码则 ``_misc``）。
    优先详情页解析 URL，再 curl，最后 em_get/requests；校验 %PDF 魔数。
    返回 **dict 信封**：``path`` / ``text_path`` / ``publish_date`` / ``note``；
    **cached=True 只表示该文件已在磁盘，不等于列表最新**。
    """
    record = record or {}
    info_code = record.get("infoCode") or record.get("info_code") or ""
    encode_url = record.get("encodeUrl") or ""
    if not info_code and not encode_url:
        return result_err(
            "missing infoCode/encodeUrl",
            source="download_pdf",
            path=None,
            pdf_url=None,
            text_path=None,
            note=REPORT_CACHE_NOTE,
        )

    code_seg = _report_code_from_record(record)
    publish_date = _publish_date_from_record(record)
    raw_name = "_".join(
        [
            publish_date or (record.get("publishDate") or record.get("publish_date") or "")[:10],
            record.get("orgSName") or record.get("org_name") or "未知",
            record.get("title", ""),
        ]
    )
    filename = document_cache_filename(
        raw_name,
        identity={
            "provider": "eastmoney_report",
            "code": code_seg,
            "info_code": str(info_code),
            "encode_url": str(encode_url),
            "file_url": str(
                record.get("pdf_url") or record.get("pdfUrl") or record.get("file_url") or ""
            ),
            "detail_pdf_url": str((detail or {}).get("pdf_url") or ""),
        },
        suffix=".pdf",
    )
    out_dir = Path(target_dir) if target_dir else default_reports_dir(code_seg)
    target = out_dir / filename

    def _ok_payload(path, content, pdf_url, cached):
        text_path = None
        text_error = None
        if extract_text:
            text_path, text_error = _write_report_text_sidecar(path, content)
        note = REPORT_CACHE_NOTE
        if cached:
            note = "cached=True 仅表示该 PDF 已在本地；" + REPORT_CACHE_NOTE
        return result_ok(
            source="download_pdf",
            path=str(path),
            text_path=text_path,
            text_error=text_error,
            partial=bool(text_error),
            pdf_url=pdf_url,
            bytes=path.stat().st_size if path.is_file() else len(content or b""),
            cached=cached,
            file_format="pdf",
            code=code_seg,
            publish_date=publish_date or _parse_date_from_filename(path.name),
            title=record.get("title") or "",
            org=record.get("orgSName") or record.get("org_name") or "",
            note=note,
        )

    if target.exists() and target.stat().st_size > 1000:
        with target.open("rb") as handle:
            head = handle.read(4)
            rest = handle.read()
        if head == b"%PDF":
            content = head + rest
            return _ok_payload(target, content, None, True)
        target.unlink(missing_ok=True)

    try:
        candidates, detail = _pdf_candidate_urls(record, kind=kind, detail=detail)
    except Exception as exc:
        return result_err(
            exc,
            source="download_pdf",
            path=None,
            pdf_url=None,
            text_path=None,
            note=REPORT_CACHE_NOTE,
        )
    if not candidates:
        return result_err(
            "no pdf candidates",
            source="download_pdf",
            path=None,
            pdf_url=(detail or {}).get("pdf_url"),
            text_path=None,
            note=REPORT_CACHE_NOTE,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    last_url = None
    for index, url in enumerate(candidates):
        last_url = url
        if index and pause_sec:
            time.sleep(pause_sec)
        content = _download_bytes_curl(url) or _download_bytes_requests(url)
        if content and content.startswith(b"%PDF"):
            atomic_write_bytes(target, content)
            return _ok_payload(target, content, url, False)
    return result_err(
        "download failed or not PDF",
        source="download_pdf",
        path=None,
        pdf_url=last_url,
        text_path=None,
        note=REPORT_CACHE_NOTE,
    )


_DETAIL_URL_TEMPLATES = {
    "industry": "https://data.eastmoney.com/report/zw_industry.jshtml?encodeUrl=%s",
    "stock": "https://data.eastmoney.com/report/zw_stock.jshtml?encodeUrl=%s",
    "strategy": "https://data.eastmoney.com/report/zw_strategy.jshtml?encodeUrl=%s",
    "macro": "https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl=%s",
    "morning": "https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl=%s",
}
