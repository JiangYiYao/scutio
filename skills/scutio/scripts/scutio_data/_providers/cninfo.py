"""巨潮公告发现、证券身份验证及官方附件解析。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlparse

from scutio_data._documents.hints import FALLBACK_HINT_HK
from scutio_data._runtime import http
from scutio_data._runtime.environment import CN_TZ, UA
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import require_a_share, require_market

_HK_TITLE_HINTS = {
    "annual": ("年报", "年報", "annual report"),
    "semi": ("中期报告", "中期報告", "半年度", "interim report"),
    "q1": ("季度业绩", "季度業績", "三个月业绩", "三个月業績"),
    "q3": ("季度业绩", "季度業績", "九个月", "九個月"),
}


def _hk_code(code: str) -> str:
    _, _, pure = require_market(code, {"hk"}, "periodic_reports_hk")
    if pure.isdigit():
        return pure.zfill(5)
    return pure


def _hk_match_kind(item: dict, kind: str) -> bool:
    """Classify discovered titles only; quarterly report periods require original text."""
    title = str(item.get("title") or "").lower()
    hints = (
        ("年报", "年報", "中期报告", "中期報告", "季度业绩", "季度業績", "annual report", "interim")
        if kind == "all"
        else _HK_TITLE_HINTS.get(kind, ())
    )
    return any(hint.lower() in title for hint in hints)


def periodic_reports_hk(code, kind="annual", page_size=20, page_num=1):
    """HK reports discovered by CNINFO keyword, then checked against exact security identity."""
    from datetime import datetime, timedelta

    from scutio_data._providers.akshare.client import fetch
    from scutio_data._runtime.environment import CN_TZ

    try:
        pure = _hk_code(code)
        if kind not in ("annual", "semi", "q1", "q3", "all"):
            raise ValueError("invalid periodic report kind")
        size, page = max(1, int(page_size)), max(1, int(page_num))
        end = datetime.now(CN_TZ).date()
        start = end - timedelta(days=365 * 20)
        # Pinned AKShare fails before HTTP for nonempty HK symbol. Keyword code
        # searches work; exact code/link checks below prevent fuzzy matches.
        rows = fetch(
            "stock_zh_a_disclosure_report_cninfo",
            symbol="",
            market="港股",
            keyword=pure,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        items = []
        for row in rows:
            if str(row.get("代码") or "").zfill(5) != pure:
                continue
            item = map_cninfo_announcement_row(row, market="hk")
            title = item["title"].lower()
            if not _hk_match_kind({"title": title}, kind):
                continue
            item.update(kind=kind, source="akshare_cninfo_hk", file_format="pdf")
            items.append(item)
        items = list({row["announcement_id"]: row for row in items}.values())
        items.sort(key=lambda row: (row["date"], row["announcement_id"]), reverse=True)
        return result_list(
            items[(page - 1) * size : page * size],
            source="periodic_reports_hk",
            adapter="akshare",
            market="hk",
            kind=kind,
            code=pure,
            total=len(items),
            partial=True,
            query_window={"start": str(start), "end": str(end)},
            coverage={"all_history": False, "classification": "title", "pdf_url_in_list": False},
            note="巨潮代码关键词发现后精确校验；标题分类，季度业绩需核对原文统计期",
            fallback_hint=FALLBACK_HINT_HK,
        )
    except Exception as exc:
        return result_list_err(
            str(exc), source="periodic_reports_hk", market="hk", fallback_hint=FALLBACK_HINT_HK
        )


_CNINFO_STATIC = "https://static.cninfo.com.cn/"


def map_cninfo_announcement_row(row: dict, *, market="a") -> dict:
    """AKShare 公告行；原文 PDF 在下载时通过官方详情解析。"""
    url = str(row.get("公告链接") or "")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    ann_id = (query.get("announcementId") or [""])[0]
    pure = str(row.get("代码") or "").zfill(5 if market == "hk" else 6)
    if parsed.hostname != "www.cninfo.com.cn" or not ann_id.isdigit():
        raise ValueError("invalid CNINFO announcement link")
    if (query.get("stockCode") or [""])[0] != pure:
        raise ValueError("CNINFO announcement link identity mismatch")
    day = str(row.get("公告时间") or "")[:10]
    datetime.strptime(day, "%Y-%m-%d")
    return {
        "title": re.sub(r"</?em>", "", str(row.get("公告标题") or "")),
        "date": day,
        "announcement_id": ann_id,
        "url": parsed._replace(scheme="https").geturl(),
        "pdf_url": None,
        "type": "",
        "adjunct_type": "",
        "adjunct_size_kb": None,
        "sec_code": pure,
        "sec_name": re.sub(r"</?em>", "", str(row.get("简称") or "")),
        "market": market,
        "file_resolution": "cninfo_detail",
    }


def resolve_cninfo_file(item):
    """解析官方公告详情的附件；不从公告日期猜测 PDF 路径。"""
    meta = dict(item)
    ann_id = str(meta.get("announcement_id") or "")
    pure = str(meta.get("sec_code") or "")
    day = str(meta.get("date") or "")[:10]
    if not ann_id.isdigit() or not re.fullmatch(
        r"\d{5}" if meta.get("market") == "hk" else r"\d{6}", pure
    ):
        raise ValueError("missing CNINFO announcement identity")
    datetime.strptime(day, "%Y-%m-%d")
    response = http.post(
        "https://www.cninfo.com.cn/new/announcement/bulletin_detail",
        params={"announceId": ann_id, "flag": "false", "announceTime": day},
        headers={"User-Agent": UA},
        timeout=15,
    )
    response.raise_for_status()
    detail = response.json().get("announcement") or {}
    if str(detail.get("announcementId")) != ann_id or str(detail.get("secCode")) != pure:
        raise ValueError("CNINFO attachment identity mismatch")
    url = urljoin(_CNINFO_STATIC, str(detail.get("adjunctUrl") or ""))
    parsed = urlparse(url)
    if parsed.hostname != "static.cninfo.com.cn" or not parsed.path.lower().endswith(".pdf"):
        raise ValueError("CNINFO PDF attachment unavailable")
    meta.update(
        pdf_url=parsed._replace(scheme="https").geturl(),
        file_format="pdf",
        adjunct_type=detail.get("adjunctType"),
        adjunct_size_kb=detail.get("adjunctSize"),
    )
    meta["file_url"] = meta["pdf_url"]
    return meta


def announcement_rows(code, category=""):
    from scutio_data._providers.akshare.client import fetch

    _, _, pure = require_a_share(code, "stock_announcements")
    end = datetime.now(CN_TZ).date()
    # Explicit finite window avoids an unbounded all-history crawl.
    start = end - timedelta(days=365 * (20 if category else 1))
    rows = fetch(
        "stock_zh_a_disclosure_report_cninfo",
        symbol=pure,
        market="沪深京",
        category=category,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if any(str(row.get("代码") or "").zfill(6) != pure for row in rows):
        raise ValueError("AKShare announcement identity mismatch")
    items = [map_cninfo_announcement_row(row) for row in rows]
    items = list({item["announcement_id"]: item for item in items}.values())
    items.sort(key=lambda item: (item["date"], item["announcement_id"]), reverse=True)
    return items, {"start": str(start), "end": str(end)}
