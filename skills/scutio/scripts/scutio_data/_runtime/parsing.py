"""源数据的有限数值、证券代码及日期转换。"""

from __future__ import annotations

import math
import re


def finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        if value in (None, "", "-"):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def formatted_number(value: object) -> float | None:
    """Read display numbers in their input units; percent signs do not rescale."""
    if value is None or isinstance(value, bool):
        return None
    return finite_number(str(value).replace(",", "").replace("%", ""))


def source_a_code(value) -> str:
    """Normalize common upstream A-share code shapes without guessing a market."""
    text = str(value or "").strip().upper()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match:
        return match.group(1)
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return ""


def source_date(value: object) -> str:
    raw = str(value or "").strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        return "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])
    return raw[:10]
