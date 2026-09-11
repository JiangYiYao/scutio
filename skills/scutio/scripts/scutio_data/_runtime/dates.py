"""日期参数与源日期解析；参数日期须通过日历有效性校验。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from scutio_data._runtime.environment import CN_TZ


def iso_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text or text.lower() in ("-", "--", "nat", "nan", "none"):
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return "%s-%s-%s" % (digits[:4], digits[4:6], digits[6:8])
    return text[:10]


def date_arg(value: Any, *, default: date) -> date:
    text = iso_date(value) if value is not None else default.isoformat()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("invalid date %r; expected YYYY-MM-DD/YYYMMDD" % value) from exc


def normalize_pool_date(date) -> str:
    """涨停池日期归一为 ``YYYYMMDD``（东财/同花顺约定）。

    接受 ``20260731`` / ``2026-07-31`` / ``2026/07/31``；``None``/空串 → 今天。
    非法格式尽量抽出 8 位数字，否则原样返回字符串（由上游决定是否空结果）。
    """
    if date is None or str(date).strip() == "":
        return datetime.now(CN_TZ).strftime("%Y%m%d")
    raw = str(date).strip()
    digits = raw.replace("-", "").replace("/", "").replace(".", "")
    if len(digits) >= 8 and digits[:8].isdigit():
        return digits[:8]
    for fmt, n in (("%Y-%m-%d", 10), ("%Y/%m/%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(raw[:n], fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return raw
