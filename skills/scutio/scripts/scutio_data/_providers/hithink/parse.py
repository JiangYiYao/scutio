"""Pure mappings for Financial API v1; missing values remain missing."""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from scutio_data._providers.quote_parse import quote_volume


def number(value, required=False):
    if value is None and not required:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid numeric field")
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    raise ValueError("invalid numeric field")


def date_ms(value):
    return (
        datetime.fromtimestamp(number(value, True) / 1000, ZoneInfo("Asia/Shanghai"))
        .date()
        .isoformat()
    )


def quote(row, meta, provenance):
    if row.get("thscode") != meta["thscode"]:
        raise ValueError("quote identity mismatch")
    pure, exchange = meta["thscode"].split(".")
    amount = number(row.get("turnover"))
    price = number(row.get("last_price"), True)
    if price <= 0:
        raise ValueError("quote price unavailable")
    return {
        "symbol": exchange.lower() + pure,
        "code": pure,
        "exchange": exchange.lower(),
        "name": meta.get("name", ""),
        "price": price,
        "last_close": number(row.get("prev_price")),
        "open": number(row.get("open_price")),
        "high": number(row.get("high_price")),
        "low": number(row.get("low_price")),
        "change_amt": number(row.get("price_change")),
        "change_pct": number(row.get("price_change_ratio_pct")),
        **quote_volume(number(row.get("volume")), "share"),
        "amount": amount,
        "amount_wan": amount / 10000 if amount is not None else None,
        "currency": "CNY",
        "pe_ttm": None,
        "pe_static": None,
        "pb": None,
        "mcap_yi": None,
        "float_mcap_yi": None,
        **provenance,
        "time": None,
        "data_as_of": None,
        "coverage": {"timestamp": False, "valuation": False},
        "partial": True,
        "warning": "quote time unavailable; provider_timestamp is not a verified quote time",
    }


def bars(rows, thscode, provenance):
    result = {}
    for row in rows:
        if row.get("thscode", thscode) != thscode:
            raise ValueError("bar identity mismatch")
        day = date_ms(row.get("date_ms"))
        volume = number(row.get("volume"))
        result[day] = {
            "datetime": day,
            **{
                key: number(row.get(key + "_price"), True)
                for key in ("open", "high", "low", "close")
            },
            "vol": volume,
            "volume": volume,
            "volume_unit": "share",
            "amount": number(row.get("turnover")),
            "amount_unit": "CNY",
            **provenance,
        }
    return [result[day] for day in sorted(result)]


def dividends(rows, thscode):
    result = []
    for row in rows:
        if row.get("thscode", thscode) != thscode:
            raise ValueError("dividend identity mismatch")
        value = number(row.get("dividend_per_share"))
        result.append(
            {
                "date": date_ms(row.get("ex_date_ms")),
                "dividend_per_share": value,
                "bonus_rmb": value * 10 if value is not None else None,
                "per_share_bonus": number(row.get("per_share_bonus")),
                "bonus_ratio": None,
                "transfer_ratio": None,
                "record_date": None,
                "payment_date": None,
                "source": "hithink",
            }
        )
    return sorted(result, key=lambda row: row["date"], reverse=True)
