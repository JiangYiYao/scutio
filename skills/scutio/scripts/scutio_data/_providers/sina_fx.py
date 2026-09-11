"""新浪带源时间 USDCNY 单符号报价。"""

from __future__ import annotations

import re
from typing import Optional

from scutio_data._runtime import http
from scutio_data._runtime.environment import UA
from scutio_data._runtime.parsing import finite_number

_SINA_HQ = "https://hq.sinajs.cn/list="


_SINA_HDR = {
    "User-Agent": UA,
    "Referer": "https://finance.sina.com.cn/",
}


def parse_sina_hq(text: str, code: str) -> Optional[dict]:
    """Only parse the audited USDCNY quote, requiring an exact source identifier."""
    if code != "fx_susdcny" or not text:
        return None
    match = re.search(r'\bhq_str_fx_susdcny="([^"]*)"', text)
    if not match or not match.group(1).strip():
        return None
    parts = match.group(1).split(",")
    if len(parts) < 9:
        return None
    return {
        "code": code,
        "name": parts[9] if len(parts) > 9 else code,
        "price": finite_number(parts[8]),
        "bid": finite_number(parts[1]),
        "ask": finite_number(parts[2]),
        "open": finite_number(parts[5]),
        "prev_close": finite_number(parts[3]),
        "high": finite_number(parts[6]),
        "low": finite_number(parts[7]),
        "time": parts[0],
        "date": parts[17] if len(parts) > 17 else None,
        "change_pct": finite_number(parts[10]) if len(parts) > 10 else None,
        "source": "sina_hq",
        "raw_n": len(parts),
    }


def _sina_hq_fetch(code: str) -> dict:
    if code != "fx_susdcny":
        raise ValueError("direct macro quote only supports USDCNY with source timestamp")
    r = http.get(_SINA_HQ + code, headers=_SINA_HDR, timeout=12)
    r.encoding = "gbk"
    parsed = parse_sina_hq(r.text, code)
    if not parsed or parsed.get("price") is None:
        raise RuntimeError("sina_hq empty or unparsed code=%s" % code)
    return parsed
