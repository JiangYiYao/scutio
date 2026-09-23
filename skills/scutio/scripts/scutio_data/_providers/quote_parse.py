"""纯解析：腾讯/新浪报价文本与腾讯 fqkline JSON → 统一字段 dict。

无网络 I/O。供 ``market`` 门面与单测直接调用；行为变更应落在本模块并加 fixture。"""

from __future__ import annotations

import math

from scutio_data._runtime.parsing import finite_number
from scutio_data._runtime.symbols import get_prefix

__all__ = [
    "amount_pair",
    "parse_eastmoney_quote",
    "parse_tencent_quote_raw",
    "parse_sina_quote_raw",
    "index_quote_rows",
    "normalize_bar",
    "quote_volume",
]


def amount_pair(raw, *, raw_unit="yuan"):
    """成交额统一为 ``(amount, amount_wan)``。

    契约（报价行，跨 A/港/美、跨源）：

    - ``amount``：本币**元**（CNY/HKD/USD，见行内 ``currency``）
    - ``amount_wan``：本币**万元**；恒有 ``amount_wan ≈ amount / 10000``
    - 禁止调用方再二次 /10000

    ``raw_unit``：

    - ``"yuan"``：上游已是元（东财 f48、新浪、腾讯港/美等）
    - ``"wan"``：上游已是万元（**腾讯 A 股** ``qt`` 成交额字段）
    """
    try:
        v = float(raw or 0)
    except (TypeError, ValueError):
        v = 0.0
    if not v:
        return 0.0, 0.0
    unit = (raw_unit or "yuan").lower()
    if unit == "wan":
        amount_wan = round(v, 4)
        amount = round(v * 10000.0, 4)
    else:
        amount = round(v, 4)
        amount_wan = round(v / 10000.0, 4)
    return amount, amount_wan


# 腾讯 qt.gtimg.cn ~ 分隔字段下标（A/港/美协议共享核心位；变更须同步 fixture）
# 文档化：避免散落魔法数字；字段中间插入仍会导致错位，靠 len 门槛 + self_check。
_TQ_NAME = 1


_TQ_CODE = 2


_TQ_PRICE = 3


_TQ_LAST_CLOSE = 4


_TQ_OPEN = 5


_TQ_VOLUME = 6


_TQ_TIME = 30


_TQ_CHANGE_AMT = 31


_TQ_CHANGE_PCT = 32


_TQ_HIGH = 33


_TQ_LOW = 34


_TQ_CURRENCY_US = 35


_TQ_AMOUNT = 37


_TQ_TURNOVER = 38


_TQ_PE_TTM = 39


_TQ_AMPLITUDE = 43


_TQ_FLOAT_MCAP = 44  # 流通市值；A/港/美实时样本一致


_TQ_MCAP = 45  # 总市值


_TQ_PB_A = 46


_TQ_LIMIT_UP = 47


_TQ_LIMIT_DOWN = 48


_TQ_VOL_RATIO_A = 49


# 港/美扩展段 46 起布局不同：46 为英文名，48/49 为 52 周高低。
# 港股量比为 50、PB 为 58；美股 PB 为 51，不能共用下标。
_TQ_VOL_RATIO_HK = 50


_TQ_PB_US = 51


_TQ_PB_HK = 58


_TQ_PE_DYNAMIC = 52  # A-share dynamic PE; not annual/static PE or HK/US layout.


_TQ_MIN_A = 53


_TQ_MIN_HK_US_EXT = 35


def _fnum(values, i, default=None):
    value = finite_number(values[i]) if i < len(values) else None
    return value if value is not None else default


def _quote_amount(raw, *, raw_unit):
    """Quote-only missing semantics; the historical amount_pair contract is unchanged."""
    value = finite_number(raw)
    if value is None or value < 0:
        return None, None
    return amount_pair(value, raw_unit=raw_unit)


def quote_volume(raw, raw_unit="share"):
    """Normalize known quote units without inventing odd-lot precision."""
    try:
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            value = None
    except (TypeError, ValueError):
        value = None
    scale = {"share": 1, "lot": 100}.get(raw_unit)
    return {
        "volume": value * scale if value is not None and scale else None,
        "volume_unit": "share",
        "volume_raw": value,
        "volume_raw_unit": raw_unit,
        "volume_precision": scale,
    }


def index_quote_rows(by_symbol):
    """Full symbol keys always; bare code when unambiguous."""
    code_hits = {}
    for symbol, row in by_symbol.items():
        code_hits[row["code"]] = code_hits.get(row["code"], 0) + 1
    out = {}
    for symbol, row in by_symbol.items():
        out[symbol] = row
        if code_hits[row["code"]] == 1:
            out[row["code"]] = row
    return out


def parse_eastmoney_quote(data, *, prefix, pure):
    """Normalize one verified push2 quote without turning missing values into zero."""
    values = {
        key: finite_number(data.get(field))
        for key, field in {
            "price": "f43",
            "last_close": "f60",
            "open": "f46",
            "high": "f44",
            "low": "f45",
            "amount": "f48",
            "change_amt": "f169",
            "change_pct": "f170",
            "turnover_pct": "f168",
            "vol_ratio": "f50",
            "mcap_yi": "f116",
            "float_mcap_yi": "f117",
        }.items()
    }
    for key in ("price", "last_close", "open", "high", "low"):
        if values[key] is not None and values[key] <= 0:
            values[key] = None
    if values["price"] is None and values["last_close"] is None:
        return None
    for key in ("amount", "mcap_yi", "float_mcap_yi"):
        if values[key] is not None and values[key] < 0:
            values[key] = None
    for key in ("mcap_yi", "float_mcap_yi"):
        if values[key] is not None:
            values[key] = round(values[key] / 1e8, 4)
    amount = values["amount"]
    last, high, low = (values[key] for key in ("last_close", "high", "low"))
    row = {
        **values,
        "name": data.get("f58") or "",
        **quote_volume(
            finite_number(data.get("f47")), "share" if prefix in ("hk", "us") else "lot"
        ),
        "amount_wan": round(amount / 10000, 4) if amount is not None else None,
        "amplitude_pct": round((high - low) / last * 100, 4)
        if last is not None and high is not None and low is not None
        else None,
        "pe_ttm": None,
        "pe_static": None,
        "pb": None,
        "limit_up": None,
        "limit_down": None,
        "currency": {"hk": "HKD", "us": "USD"}.get(prefix, "CNY"),
        "exchange": prefix,
        "symbol": prefix + pure,
        "code": pure,
        "source": "eastmoney",
        "time": "",
    }
    fields = ("price", "last_close", "open", "high", "low", "volume", "amount")
    missing = [key for key in fields if row[key] is None]
    row.update(
        coverage={key: row[key] is not None for key in fields},
        missing_fields=missing,
        partial=bool(missing),
        warning="quote fields unavailable: " + ", ".join(missing) if missing else None,
    )
    return row


def normalize_bar(bar, source):
    """归一化 K 线字段；缺失或非有限数值保留为 None，交由门面判定能否使用。"""
    out = {
        "datetime": bar.get("datetime") or bar.get("date") or bar.get("time") or "",
        "open": finite_number(bar.get("open")),
        "high": finite_number(bar.get("high")),
        "low": finite_number(bar.get("low")),
        "close": finite_number(bar.get("close")),
        "vol": finite_number(bar.get("vol", bar.get("volume"))),
        "volume": finite_number(bar.get("volume", bar.get("vol"))),
        "amount": finite_number(bar.get("amount")),
        "source": source,
    }
    return out


def parse_tencent_quote_raw(raw_text):
    """解析腾讯 ``qt.gtimg.cn`` GBK 响应为 symbol→row 字典（不含裸码索引）。"""
    by_symbol = {}
    if not raw_text:
        return by_symbol
    for line in str(raw_text).strip().split(";"):
        if "=" not in line or '"' not in line:
            continue
        left = line.split("=")[0].strip()
        key = left.split("_", 1)[-1] if "_" in left else left
        if key.startswith("pv_none") or "none_match" in key:
            continue
        values = line.split('"')[1].split("~")
        if len(values) < 6:
            continue

        key_l = key.lower()
        if key_l.startswith("hk") and len(key) > 2:
            exchange = "hk"
            code = key[2:]
        elif key_l.startswith("us") and len(key) > 2:
            exchange = "us"
            code = key[2:].upper()
        elif len(key) > 2 and key_l[:2] in ("sh", "sz", "bj"):
            exchange = key_l[:2]
            code = key[2:]
        else:
            code = key[2:] if len(key) > 2 else key
            exchange = key_l[:2] if len(key) > 2 else get_prefix(code)

        is_hk = exchange == "hk"
        is_us = exchange == "us"

        payload_code = values[_TQ_CODE].strip().upper()
        if is_us:
            # The payload uses venue-qualified symbols such as AAPL.OQ.
            # Remove only a known venue suffix; BRK.B must retain its class.
            if payload_code != code:
                for suffix in (".OQ", ".N", ".A"):
                    if payload_code.endswith(suffix):
                        payload_code = payload_code[: -len(suffix)]
                        break
            same_identity = payload_code == code
        else:
            same_identity = payload_code.isdigit() and payload_code.zfill(len(code)) == code
        if not same_identity:
            continue

        if is_us:
            if len(values) < _TQ_MIN_HK_US_EXT:
                if len(values) < 7:
                    continue
            # 腾讯美股成交额：本币元
            amount, amount_wan = _quote_amount(_fnum(values, _TQ_AMOUNT), raw_unit="yuan")
            pe_ttm = _fnum(values, _TQ_PE_TTM, None)
            amplitude = _fnum(values, _TQ_AMPLITUDE)
            mcap_yi = _fnum(values, _TQ_MCAP, None)
            float_mcap_yi = _fnum(values, _TQ_FLOAT_MCAP, None)
            pb = _fnum(values, _TQ_PB_US, None)
            name_en = values[_TQ_PB_A] if len(values) > _TQ_PB_A else ""
            try:
                if name_en and str(name_en).replace(".", "", 1).isdigit():
                    name_en = ""
            except Exception:
                pass
            currency = (
                values[_TQ_CURRENCY_US]
                if len(values) > _TQ_CURRENCY_US and values[_TQ_CURRENCY_US]
                else "USD"
            ) or "USD"
            if currency not in ("USD", "HKD", "CNY"):
                currency = "USD"
            limit_up = None
            limit_down = None
            vol_ratio = None
            pe_static = None
            turnover_pct = _fnum(values, _TQ_TURNOVER, None)
        elif is_hk:
            if len(values) < _TQ_MIN_HK_US_EXT:
                continue
            # 腾讯港股成交额：本币元
            amount, amount_wan = _quote_amount(_fnum(values, _TQ_AMOUNT), raw_unit="yuan")
            pe_ttm = _fnum(values, _TQ_PE_TTM, None)
            amplitude = _fnum(values, _TQ_AMPLITUDE)
            mcap_yi = _fnum(values, _TQ_MCAP, None)
            float_mcap_yi = _fnum(values, _TQ_FLOAT_MCAP, None)
            pb = _fnum(values, _TQ_PB_HK, None)
            limit_up = None
            limit_down = None
            vol_ratio = _fnum(values, _TQ_VOL_RATIO_HK, None)
            pe_static = None
            name_en = (
                values[_TQ_PB_A]
                if len(values) > _TQ_PB_A
                and not str(values[_TQ_PB_A]).replace(".", "", 1).isdigit()
                else ""
            )
            currency = "HKD"
            turnover_pct = _fnum(values, _TQ_TURNOVER, None)
        else:
            if len(values) < _TQ_MIN_A:
                continue
            # 腾讯 A 股成交额字段已是「万元」→ amount 升为元
            amount, amount_wan = _quote_amount(_fnum(values, _TQ_AMOUNT), raw_unit="wan")
            pe_ttm = _fnum(values, _TQ_PE_TTM, None)
            amplitude = _fnum(values, _TQ_AMPLITUDE)
            mcap_yi = _fnum(values, _TQ_MCAP, None)
            float_mcap_yi = _fnum(values, _TQ_FLOAT_MCAP, None)
            pb = _fnum(values, _TQ_PB_A, None)
            limit_up = _fnum(values, _TQ_LIMIT_UP, None)
            limit_down = _fnum(values, _TQ_LIMIT_DOWN, None)
            # Indices use -1 for inapplicable price limits and 0 for absent PB.
            limit_up = limit_up if limit_up is not None and limit_up > 0 else None
            limit_down = limit_down if limit_down is not None and limit_down > 0 else None
            pb = pb if pb != 0 else None
            vol_ratio = _fnum(values, _TQ_VOL_RATIO_A, None)
            pe_static = None
            name_en = ""
            currency = "CNY"
            turnover_pct = _fnum(values, _TQ_TURNOVER, None)

        symbol = exchange + code
        row = {
            "name": values[_TQ_NAME] if len(values) > _TQ_NAME else "",
            "price": _fnum(values, _TQ_PRICE),
            "last_close": _fnum(values, _TQ_LAST_CLOSE),
            "open": _fnum(values, _TQ_OPEN),
            **quote_volume(
                _fnum(values, _TQ_VOLUME, None),
                "share"
                if is_hk or is_us or (exchange == "sh" and code.startswith(("688", "689")))
                else "lot",
            ),
            "change_amt": _fnum(values, _TQ_CHANGE_AMT),
            "change_pct": _fnum(values, _TQ_CHANGE_PCT),
            "high": _fnum(values, _TQ_HIGH),
            "low": _fnum(values, _TQ_LOW),
            "amount": amount,
            "amount_wan": amount_wan,
            "turnover_pct": turnover_pct,
            "pe_ttm": pe_ttm,
            "amplitude_pct": amplitude,
            "mcap_yi": mcap_yi,
            "float_mcap_yi": float_mcap_yi,
            "pb": pb,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "vol_ratio": vol_ratio,
            "pe_static": pe_static,
            "pe_dynamic": _fnum(values, _TQ_PE_DYNAMIC, None) if not is_hk and not is_us else None,
            "currency": currency,
            "exchange": exchange,
            "symbol": symbol,
            "code": code,
            "source": "tencent",
            "time": values[_TQ_TIME] if len(values) > _TQ_TIME else "",
        }
        if name_en:
            row["name_en"] = name_en
        by_symbol[symbol] = row
    return by_symbol


def parse_sina_quote_raw(raw_text):
    """按 ``hq_str_`` 证券键解析新浪 ``hq.sinajs.cn`` 响应。"""
    out = {}
    if not raw_text:
        return out

    for line in str(raw_text).replace("\r", "").split("\n"):
        line = line.strip()
        if "hq_str_" not in line or '="' not in line:
            continue
        try:
            left, right = line.split("=", 1)
        except ValueError:
            continue
        key = left.split("hq_str_")[-1].strip()
        if not right.startswith('"'):
            continue
        payload = right.strip().rstrip(";").strip()
        if payload.startswith('"') and payload.endswith('"'):
            payload = payload[1:-1]
        if not payload:
            continue
        parts = payload.split(",")
        if len(parts) < 3:
            continue

        key_l = key.lower()
        row = None
        if key_l.startswith("rt_hk") or key_l.startswith("hk"):
            pure = key_l.replace("rt_hk", "").replace("hk", "", 1)
            if len(parts) < 7:
                continue
            price = _fnum(parts, 6)
            last_close = _fnum(parts, 3)
            amount, amount_wan = _quote_amount(_fnum(parts, 11), raw_unit="yuan")
            row = {
                "name": parts[1] or parts[0],
                "name_en": parts[0],
                "price": price,
                "last_close": last_close,
                "open": _fnum(parts, 2),
                "high": _fnum(parts, 4),
                "low": _fnum(parts, 5),
                "change_amt": _fnum(parts, 7),
                "change_pct": _fnum(parts, 8),
                "amount": amount,
                **quote_volume(_fnum(parts, 12, None)),
                "amount_wan": amount_wan,
                "turnover_pct": None,
                "pe_ttm": None,
                "amplitude_pct": None,
                "mcap_yi": None,
                "float_mcap_yi": None,
                "pb": None,
                "limit_up": None,
                "limit_down": None,
                "vol_ratio": None,
                "pe_static": None,
                "currency": "HKD",
                "exchange": "hk",
                "symbol": "hk" + pure.zfill(5) if pure.isdigit() else "hk" + pure,
                "code": pure.zfill(5) if pure.isdigit() else pure,
                "source": "sina",
                "time": (
                    "%s %s" % (parts[17], parts[18])
                    if len(parts) > 18
                    else (parts[17] if len(parts) > 17 else "")
                ),
            }
        elif key_l.startswith("gb_"):
            ticker = key_l[3:].upper().replace("$", ".").replace("_", ".")
            if len(parts) < 8:
                continue
            price = _fnum(parts, 1)
            # The US feed exposes market capitalization at 12, not turnover.
            # No traded-value field is available; price * volume is not a substitute.
            market_cap = finite_number(parts[12]) if len(parts) > 12 else None
            if market_cap is not None and market_cap < 0:
                market_cap = None
            row = {
                "name": parts[0],
                "price": price,
                "last_close": None,
                "open": _fnum(parts, 5),
                "high": _fnum(parts, 6),
                "low": _fnum(parts, 7),
                "change_amt": _fnum(parts, 4, None),
                "change_pct": _fnum(parts, 2),
                **quote_volume(_fnum(parts, 10, None)),
                "amount": None,
                "amount_wan": None,
                "turnover_pct": None,
                "pe_ttm": _fnum(parts, 14, None),
                "amplitude_pct": None,
                "mcap_yi": market_cap / 1e8 if market_cap is not None else None,
                "float_mcap_yi": None,
                "pb": None,
                "limit_up": None,
                "limit_down": None,
                "vol_ratio": None,
                "pe_static": None,
                "currency": "USD",
                "exchange": "us",
                "symbol": "us" + ticker,
                "code": ticker,
                "source": "sina",
                "time": parts[3] if len(parts) > 3 else "",
                "partial": True,
                "coverage": {"amount": False},
                "warning": "Sina US quote does not provide turnover amount",
            }
            if price and row["change_amt"] is not None:
                row["last_close"] = round(price - row["change_amt"], 4)
        else:
            if len(parts) < 4:
                continue
            if len(key_l) >= 8 and key_l[:2] in ("sh", "sz", "bj"):
                exchange = key_l[:2]
                pure = key_l[2:]
            else:
                continue
            price = _fnum(parts, 3)
            last_close = _fnum(parts, 2)
            # 新浪 A 股成交额：元
            amount, amount_wan = _quote_amount(_fnum(parts, 9), raw_unit="yuan")
            high, low = _fnum(parts, 4), _fnum(parts, 5)
            change = price - last_close if price is not None and last_close is not None else None
            row = {
                "name": parts[0],
                "price": price,
                "last_close": last_close,
                "open": _fnum(parts, 1),
                "high": high,
                "low": low,
                **quote_volume(_fnum(parts, 8, None)),
                "amount": amount,
                "change_amt": round(change, 4) if change is not None else None,
                "change_pct": round(change / last_close * 100.0, 4)
                if change is not None and last_close
                else None,
                "amount_wan": amount_wan,
                "turnover_pct": None,
                "pe_ttm": None,
                "amplitude_pct": (
                    round((high - low) / last_close * 100, 4)
                    if last_close and high is not None and low is not None
                    else None
                ),
                "mcap_yi": None,
                "float_mcap_yi": None,
                "pb": None,
                "limit_up": None,
                "limit_down": None,
                "vol_ratio": None,
                "pe_static": None,
                "currency": "CNY",
                "exchange": exchange,
                "symbol": exchange + pure,
                "code": pure,
                "source": "sina",
                "time": f"{parts[30]} {parts[31]}" if len(parts) > 31 else "",
            }
        if row and (row.get("price") or row.get("last_close")):
            out[row["symbol"]] = row
    return out
