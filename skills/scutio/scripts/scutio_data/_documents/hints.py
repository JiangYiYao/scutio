"""原文缺失时的公开检索入口提示。"""

from __future__ import annotations

# Agent 必读：toolkit 挂了时不要硬编，去公开站检索下载
FALLBACK_HINT_US = (
    "toolkit 港美披露失败或空时：Agent 应自行检索下载，勿编造。"
    "美股 → SEC EDGAR（https://www.sec.gov/edgar/searchedgar/companysearch.html "
    "或 https://efts.sec.gov/LATEST/search-index ）按 ticker 查 10-K/10-Q/20-F；"
    "或公司 IR。下载后可读 HTML/PDF，摘要须标注来源 URL。"
)


FALLBACK_HINT_HK = (
    "toolkit 港美披露失败或空时：Agent 应自行检索下载，勿编造。"
    "港股 → HKEXnews Title Search（https://www1.hkexnews.hk/search/titlesearch.xhtml ）"
    "按股份代号查年报/中期报告 PDF；或公司 IR。"
    "下载后摘要须标注来源 URL。"
)


FALLBACK_HINT_A = (
    "toolkit 巨潮失败时：可改查巨潮披露 https://www.cninfo.com.cn/ 或交易所公告页；勿编造年报内容。"
)


def fallback_hint_for_market(market: str) -> str:
    m = (market or "").strip().lower()
    if m == "us":
        return FALLBACK_HINT_US
    if m == "hk":
        return FALLBACK_HINT_HK
    if m == "a":
        return FALLBACK_HINT_A
    return FALLBACK_HINT_US + " " + FALLBACK_HINT_HK
