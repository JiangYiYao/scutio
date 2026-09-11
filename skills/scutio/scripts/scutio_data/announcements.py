"""A 股与港美公告、定期报告、互动问答及解禁的公开入口。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from scutio_data._documents.filings import (
    download_announcement_pdf,
    extract_filing_text,
)
from scutio_data._providers.cninfo import announcement_rows, map_cninfo_announcement_row
from scutio_data._runtime.results import (
    result_err,
    result_list,
    result_list_err,
    result_ok,
)
from scutio_data._runtime.symbols import require_a_share, require_company
from scutio_data._runtime.timeouts import operation

__all__ = [
    "PERIODIC_REPORT_KINDS",
    "stock_announcements",
    "periodic_reports",
    "download_announcement_pdf",
    "irm",
    "lockup_expiry",
    "map_cninfo_announcement_row",
    "extract_filing_text",
]


# AKShare 巨潮定期报告分类
_PERIODIC_CATEGORY = {
    "annual": "年报",  # 年报
    "semi": "半年报",  # 半年报
    "q1": "一季报",  # 一季报
    "q3": "三季报",  # 三季报
}


PERIODIC_REPORT_KINDS = frozenset(list(_PERIODIC_CATEGORY.keys()) + ["all"])


_A_PERIODIC_NON_FULL_TOKENS = ("摘要", "英文版", "英文版本", "english version", "取消")


_A_PERIODIC_REVISION_TOKENS = ("修订版", "修正版", "更正后", "更新后")


@operation("history")
def stock_announcements(code, page_size=30, page_num=1):
    """近一年巨潮公告，本地分页；PDF 在下载时解析。仅 A 股。"""
    try:
        _, _, pure = require_a_share(code, "stock_announcements")
        size, page = max(1, int(page_size)), max(1, int(page_num))
        items, window = announcement_rows(pure)
        return result_list(
            items[(page - 1) * size : page * size],
            source="stock_announcements",
            market="a",
            adapter="akshare",
            query_window=window,
            available_count=len(items),
            partial=True,
            coverage={"all_history": False, "pdf_url_in_list": False},
            warning="List covers the last year; attachment URLs resolve on download",
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="stock_announcements",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


def _map_periodic_report_a(row: dict, kind: str) -> dict:
    item = dict(row)
    item["kind"] = kind
    item["market"] = "a"
    item["file_format"] = "pdf"
    if item.get("pdf_url"):
        item["file_url"] = item["pdf_url"]
    return item


def _a_periodic_quality(item: dict) -> int:
    title = str(item.get("title") or "").strip().lower()
    if any(token in title for token in _A_PERIODIC_NON_FULL_TOKENS):
        return 0
    if any(token in title for token in _A_PERIODIC_REVISION_TOKENS):
        return 2
    return 1


def _a_periodic_family(item: dict) -> str:
    """Collapse full/summary/English variants of the same titled filing."""
    title = str(item.get("title") or "").strip().lower()
    for token in sorted(
        _A_PERIODIC_NON_FULL_TOKENS + _A_PERIODIC_REVISION_TOKENS,
        key=len,
        reverse=True,
    ):
        title = title.replace(token, "")
    return re.sub(r"[\W_]+", "", title, flags=re.UNICODE)


def _select_a_periodic_items(items: List[dict], limit: int) -> Tuple[List[dict], int]:
    """Prefer complete Chinese filings while retaining variants as last resort."""
    families: Dict[str, List[dict]] = {}
    for item in items:
        families.setdefault(_a_periodic_family(item), []).append(item)
    pool: List[dict] = []
    for variants in families.values():
        preferred = [item for item in variants if _a_periodic_quality(item) > 0]
        pool.extend(preferred or variants)
    pool.sort(
        key=lambda item: (
            item.get("date") or "",
            _a_periodic_quality(item),
            item.get("announcement_id") or "",
        ),
        reverse=True,
    )
    return pool[: max(1, int(limit))], max(0, len(items) - len(pool))


def _periodic_reports_a(code, kind="annual", page_size=20, page_num=1):
    _, _, pure = require_a_share(code, "periodic_reports")
    key = str(kind or "annual").strip().lower()
    size, page = max(1, int(page_size)), max(1, int(page_num))
    categories = _PERIODIC_CATEGORY if key == "all" else {key: _PERIODIC_CATEGORY[key]}
    items, errors, window = [], {}, None
    for report_kind, category in categories.items():
        try:
            rows, window = announcement_rows(pure, category)
            items.extend(_map_periodic_report_a(row, report_kind) for row in rows)
        except Exception as exc:
            errors[report_kind] = str(exc)
    if len(errors) == len(categories):
        return result_list_err(
            "; ".join(errors.values()), source="periodic_reports", market="a", errors=errors
        )
    selected, excluded = _select_a_periodic_items(items, max(1, len(items)))
    return result_list(
        selected[(page - 1) * size : page * size],
        source="periodic_reports",
        market="a",
        kind=key,
        adapter="akshare",
        query_window=window,
        partial=True,
        errors=errors,
        available_count=len(selected),
        excluded_non_full_variants=excluded,
        coverage={"all_history": False, "pdf_url_in_list": False},
        warning="List covers the last 20 years; attachment URLs resolve on download",
    )


@operation("history")
def periodic_reports(code, kind="annual", page_size=20, page_num=1):
    """定期报告列表（年报/半年报/季报），含原文链接。

    **市场**：
      - A：巨潮栏目（PDF）
      - 美：SEC EDGAR（多为 HTML 原文，``file_format=html``）
      - 港：AKShare 巨潮代码关键词查询（有限窗口、标题分类，PDF）

    Args:
        code: ``600519`` / ``hk00700`` / ``usAAPL``。
        kind: ``annual`` | ``semi`` | ``q1`` | ``q3`` | ``all``。
        page_size: 条数上限。
        page_num: 过滤后的本地分页。

    Returns:
        result_list：items 含 title/date/kind/pdf_url 或 file_url 等。
    """
    key = str(kind or "annual").strip().lower()
    if key not in PERIODIC_REPORT_KINDS:
        return result_list_err(
            "unknown kind %r; choose from %s" % (kind, ",".join(sorted(PERIODIC_REPORT_KINDS))),
            source="periodic_reports",
        )
    try:
        mkt, _, _ = require_company(code, "periodic_reports")
        if mkt == "us":
            from scutio_data._providers import cninfo, sec

            return sec.periodic_reports_us(code, kind=key, page_size=page_size, page_num=page_num)
        if mkt == "hk":
            from scutio_data._providers import cninfo, sec

            return cninfo.periodic_reports_hk(
                code, kind=key, page_size=page_size, page_num=page_num
            )
        return _periodic_reports_a(code, kind=key, page_size=page_size, page_num=page_num)
    except Exception as exc:
        return result_list_err(str(exc), source="periodic_reports")


@operation("history")
def irm(code, page_size=30, page_num=1):
    """深市 AKShare 互动易问答；对最多 10000 条源记录做本地分页。"""
    try:
        _, exchange, pure = require_a_share(code, "irm")
        if exchange != "sz":
            return result_list_err(
                "unsupported_exchange: irm currently supports Shenzhen cninfo interactions only",
                source="irm",
                error_code="unsupported_exchange",
                supported_exchanges=["sz"],
            )
        size, page = int(page_size), int(page_num)
        if size < 1 or page < 1:
            raise ValueError("page_size and page_num must be positive")
        from scutio_data._providers.akshare.client import fetch

        rows = fetch("stock_irm_cninfo", symbol=pure)
        if any(str(x.get("股票代码") or "").zfill(6) != pure for x in rows):
            raise ValueError("IRM stock identity mismatch")
        out = [
            {
                "code": pure,
                "company": x.get("公司简称"),
                "question": x.get("问题"),
                "answer": x.get("回答内容"),
                "answerer": x.get("回答者"),
                "ask_time": str(x.get("提问时间") or "")[:16],
                "question_id": x.get("问题编号"),
            }
            for x in rows
        ]
        out.sort(key=lambda x: x["ask_time"], reverse=True)
        start = (page - 1) * size
        return result_list(
            out[start : start + size],
            source="irm",
            adapter="akshare",
            page_num=page,
            page_size=size,
            fetched=len(rows),
            partial=len(rows) >= 10000 or start + size > 10000,
            coverage="up to 10000 source records; local pagination",
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="irm",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


@operation("history")
def lockup_expiry(code, trade_date, forward_days=90):
    """AKShare 解禁队列，股份单位为股，ratio 为流通股比例（0–1）。"""
    try:
        _, _, pure = require_a_share(code, "lockup_expiry")
        start = datetime.strptime(trade_date, "%Y-%m-%d").date()
        days = int(forward_days)
        if days < 0:
            raise ValueError("forward_days must be nonnegative")
        end = start + timedelta(days=days)
        from scutio_data._providers.akshare.client import fetch
        from scutio_data._runtime.parsing import finite_number

        rows = fetch("stock_restricted_release_queue_em", symbol=pure)
        items = []
        for row in rows:
            day = str(row["解禁时间"])[:10]
            datetime.strptime(day, "%Y-%m-%d")
            items.append(
                {
                    "date": day,
                    "type": row.get("限售股类型") or "",
                    "shares": finite_number(row.get("实际解禁数量")),
                    "able_shares": finite_number(row.get("解禁数量")),
                    "ratio": finite_number(row.get("占流通市值比例")),
                }
            )
        return result_ok(
            source="lockup_expiry",
            adapter="akshare",
            partial=len(rows) >= 500,
            coverage={"source_row_limit": 500, "possibly_truncated": len(rows) >= 500},
            units={"shares": "股", "able_shares": "股", "ratio": "fraction_of_float_shares"},
            history=sorted(
                [row for row in items if row["date"] < str(start)],
                key=lambda row: row["date"],
                reverse=True,
            ),
            upcoming=sorted(
                [row for row in items if str(start) <= row["date"] <= str(end)],
                key=lambda row: row["date"],
            ),
        )
    except Exception as exc:
        return result_err(
            str(exc),
            source="lockup_expiry",
            history=[],
            upcoming=[],
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )
