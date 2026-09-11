"""资讯文本：个股新闻检索、电报与全球资讯。

详文：``references/06-feeds.md``。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import normalize_code, require_company
from scutio_data._runtime.timeouts import operation

__all__ = [
    "stock_news",
    "telegraph",
    "global_news",
    "parse_news_time",
    "rank_stock_news_items",
]


_HTML_TAG_RE = re.compile(r"<[^>]+>")


# 板块榜/资金榜类标题：代码往往只出现在正文表格碎片里，不是公司新闻
_NOISE_TITLE_RE = re.compile(
    r"(突破半年线|站上半年线|盘中突破|中线走稳|"
    r"大宗交易超|个股大宗|"
    r"净流出资金|净流入资金超|主力资金净流入超|"
    r"概念上涨|概念下跌|行业今日净|"
    r"只个股突破|只股中线|只股主力)"
)


def parse_news_time(value: Any) -> Optional[datetime]:
    """解析新闻时间字符串；失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    # 截断常见带毫秒/时区尾巴
    s = s.replace("T", " ")[:19]
    for fmt, n in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y/%m/%d %H:%M:%S", 19),
        ("%Y/%m/%d %H:%M", 16),
        ("%Y-%m-%d", 10),
        ("%Y/%m/%d", 10),
    ):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


def _score_stock_news_item(
    item: dict,
    pure: str,
    name: Optional[str] = None,
) -> Tuple[int, bool]:
    """返回 (相关分, 是否榜单噪音标题)。"""
    title = item.get("title") or ""
    content = item.get("content") or ""
    noise = bool(_NOISE_TITLE_RE.search(title))
    score = 0
    if pure and pure in title:
        score += 10
    if pure and pure in content:
        score += 4
    if name:
        if name in title:
            score += 8
        if name in content:
            score += 3
    if noise and pure not in title and (not name or name not in title):
        # 标题是榜单且公司名/码不在标题 → 强降权
        score -= 25
    return score, noise


def rank_stock_news_items(
    items: Sequence[dict],
    code: str,
    *,
    name: Optional[str] = None,
    order: str = "time",
    page_size: int = 20,
    drop_noise: bool = True,
) -> List[dict]:
    """过滤榜单噪音并按时间/相关度排序。

    Args:
        items: 原始 items。
        code: 股票代码（会 normalize）。
        name: 可选证券简称，用于加权（如「贵州茅台」）。
        order: ``time``（默认，新→旧）| ``relevance``（相关分优先，同分再按时间）。
        page_size: 返回条数上限。
        drop_noise: 丢掉「噪音标题且码/名不在标题」的条目。
    """
    pure = normalize_code(code)
    name = (name or "").strip() or None
    order = (order or "time").strip().lower()
    if order not in ("time", "relevance"):
        order = "time"

    ranked: List[dict] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        score, noise = _score_stock_news_item(item, pure, name)
        if drop_noise and noise and pure not in (item.get("title") or ""):
            if not name or name not in (item.get("title") or ""):
                continue
        item["relevance"] = score
        ranked.append(item)

    def _ts(it: dict) -> datetime:
        return parse_news_time(it.get("time")) or datetime(1970, 1, 1)

    if order == "relevance":
        ranked.sort(key=lambda it: (it.get("relevance") or 0, _ts(it)), reverse=True)
    else:
        # 有解析时间的在前；同秒内相关分高的在前
        ranked.sort(
            key=lambda it: (
                1 if parse_news_time(it.get("time")) else 0,
                _ts(it),
                it.get("relevance") or 0,
            ),
            reverse=True,
        )
    cap = max(1, int(page_size or 20))
    return ranked[:cap]


@operation("history")
def stock_news(code, page_size=20, order="time", name=None, drop_noise=True):
    """个股相关报道（东财检索 + 本地过滤/排序）。

    这是**检索线索**，不是交易所公告时间线；公告请用 ``stock_announcements``。

    Args:
        code: A 股代码（港美覆盖不保证）。
        page_size: 返回条数。
        order: ``time``（默认新→旧）| ``relevance``（上游相关 + 本地分）。
        name: 可选简称，提高过滤准确度。
        drop_noise: 过滤板块榜/资金榜类噪音标题。

    Returns:
        result_list：items 含 title/content/time/source/url/relevance。
    """
    try:
        _, _, pure = require_company(code, "stock_news")
        from scutio_data._providers.akshare.client import fetch

        articles = fetch("stock_news_em", symbol=pure)
        raw_items = [
            {
                "title": re.sub(r"<[^>]+>", "", x.get("新闻标题") or ""),
                "content": re.sub(r"<[^>]+>", "", x.get("新闻内容") or "")[:200],
                "time": x.get("发布时间") or "",
                "source": x.get("文章来源") or "",
                "url": x.get("新闻链接") or "",
            }
            for x in articles
        ]
        ranked = rank_stock_news_items(
            raw_items,
            code,
            name=name,
            order=order,
            # Rank the full fetched candidate set first so the envelope can
            # distinguish provider-empty from locally-filtered-empty.
            page_size=max(1, len(raw_items)),
            drop_noise=drop_noise,
        )
        cap = max(1, int(page_size or 20))
        items = ranked[:cap]
        fetched_raw = len(raw_items)
        filtered_out = max(0, fetched_raw - len(ranked))
        empty_reason = None
        if not items:
            empty_reason = "upstream_empty" if fetched_raw == 0 else "all_filtered"
        return result_list(
            items,
            source="stock_news",
            order=order,
            drop_noise=bool(drop_noise),
            adapter="akshare",
            coverage="recent source window; no historical pagination",
            upstream_limit=10,
            requested_limit=cap,
            partial=cap > 10,
            fetched_raw=fetched_raw,
            after_filter=len(ranked),
            filtered_out=filtered_out,
            returned=len(items),
            truncated=len(ranked) > len(items),
            empty_reason=empty_reason,
        )
    except Exception as exc:
        return result_list_err(str(exc), source="stock_news")


@operation("history")
def telegraph(page_size=50):
    """财联社最新电报，经 AKShare 获取并按发布时间倒序。"""
    try:
        from scutio_data._providers.akshare.client import fetch

        rows = fetch("stock_info_global_cls", symbol="全部")
        items = [
            {
                "title": x.get("标题") or "",
                "content": x.get("内容") or "",
                "time": str(x.get("发布日期") or "")[:10] + " " + str(x.get("发布时间") or ""),
            }
            for x in rows
        ]
        items.sort(key=lambda x: x["time"], reverse=True)
        limit = max(1, int(page_size))
        return result_list(
            items[:limit],
            source="telegraph",
            adapter="akshare",
            upstream_limit=20,
            requested_limit=limit,
            partial=limit > 20,
            coverage="latest source window",
        )
    except Exception as exc:
        return result_list_err(str(exc), source="telegraph")


@operation("history")
def global_news(page_size=50):
    """东财全球资讯，经 AKShare 获取。"""
    try:
        from scutio_data._providers.akshare.client import fetch

        rows = fetch("stock_info_global_em")
        items = [
            {
                "title": x.get("标题") or "",
                "summary": (x.get("摘要") or "")[:200],
                "time": x.get("发布时间") or "",
                "url": x.get("链接") or "",
            }
            for x in rows
        ]
        items.sort(key=lambda x: x["time"], reverse=True)
        limit = max(1, int(page_size))
        return result_list(
            items[:limit],
            source="global_news",
            adapter="akshare",
            upstream_limit=200,
            requested_limit=limit,
            partial=limit > 200,
            coverage="latest source window",
        )
    except Exception as exc:
        return result_list_err(str(exc), source="global_news")
