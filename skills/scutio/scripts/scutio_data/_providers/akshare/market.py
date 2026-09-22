"""AKShare K 线与公司简介参数选择及字段口径校验。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scutio_data._providers.akshare import client
from scutio_data._providers.eastmoney import em_secid_candidates, remember_em_secid
from scutio_data._providers.quote_parse import normalize_bar
from scutio_data._runtime.symbols import (
    require_a_share,
    require_security,
    security_kind,
    validate_identity,
)


def _validate_bar_identity(row, code, prefix, pure):
    """Check native claims before normalization drops provider-specific columns."""
    fields = ("symbol", "stockCode", "code", "ticker", "股票代码", "证券代码", "SECURITY_CODE")
    claims = {"exchange": row.get("exchange")}
    for field in fields:
        value = row.get(field)
        if value in (None, ""):
            continue
        value = str(value).strip()
        if prefix != "us" and value.isdigit():
            value = value.zfill(len(pure))
        elif prefix == "us" and value.upper() == pure:
            value = pure
        claims[field] = value
    # An absent native identifier is permitted, but is not independent identity proof.
    try:
        validate_identity(claims, code, fields=fields, required=False)
    except ValueError as exc:
        raise ValueError("bar identity mismatch") from exc


def bars(code, frequency="D", count=80, index=None, adjust="none", source="sina"):
    _, prefix, pure = require_security(code, "security_bars")
    kind = security_kind(code)
    is_index = kind == "index"
    if index and not is_index:
        raise ValueError("unsupported_asset: index flag conflicts with security identity")
    if is_index and adjust != "none":
        raise ValueError("unsupported adjustment: index bars are unadjusted")
    if adjust not in ("none", "qfq", "hfq"):
        raise ValueError("adjust must be none, qfq or hfq")
    periods = {
        "D": "daily",
        "1D": "daily",
        "W": "weekly",
        "1W": "weekly",
        "M": "monthly",
        "1M": "monthly",
    }
    end = datetime.now(timezone.utc)
    multiplier = 7 if "W" in frequency else 31 if "M" in frequency else 1
    start = end - timedelta(days=min(36500, max(60, count * 3 * multiplier)))
    dates = {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}
    adj = "" if adjust == "none" else adjust
    if frequency not in periods:
        raise ValueError("only daily, weekly and monthly bars are supported")
    if prefix in ("hk", "us"):
        if source == "sina":
            if frequency not in ("D", "1D") or prefix == "us" and adjust == "hfq":
                raise ValueError("Sina selected market/frequency/adjustment is unsupported")
            rows = client.fetch(
                "stock_hk_daily" if prefix == "hk" else "stock_us_daily", symbol=pure, adjust=adj
            )
        elif source == "eastmoney":
            if prefix == "hk":
                rows = client.fetch(
                    "stock_hk_hist", symbol=pure, period=periods[frequency], adjust=adj, **dates
                )
            else:
                rows = []
                failures = []
                for secid in em_secid_candidates(code):
                    try:
                        rows = client.fetch(
                            "stock_us_hist",
                            symbol=secid,
                            period=periods[frequency],
                            adjust=adj,
                            **dates,
                        )
                        if rows:
                            for row in rows:
                                _validate_bar_identity(row, code, prefix, pure)
                            remember_em_secid(code, secid)
                            break
                    except RuntimeError as exc:
                        failures.append(str(exc))
                if not rows and failures:
                    raise RuntimeError("; ".join(failures))
        else:
            raise ValueError("AKShare Tencent adapter is A-market only")
    elif source == "tencent":
        if frequency not in ("D", "1D") or adjust != "none":
            # 1.18.94 selects day before qfqday/hfqday, so cannot attest adjustment.
            raise ValueError(
                "AKShare Tencent supports unadjusted daily only; adjustment series is not verified"
            )
        rows = client.fetch(
            "stock_zh_a_hist_tx",
            symbol=prefix + pure,
            start_date=dates["start_date"],
            end_date=end.date().isoformat(),
            adjust=adj,
            timeout=8,
        )
        # AKShare 1.18.94 leaves these Tencent series in lots despite documenting
        # shares. Index volumes are aggregate shares, not index points; verified
        # against Sina index history as well as Shenzhen equity daily records.
        if prefix + pure[:3] in ("sz000", "sh000", "sz399"):
            for row in rows:
                if row.get("volume") is not None:
                    row["volume"] *= 100
    elif source == "sina":
        if frequency in ("D", "1D") and not is_index and prefix != "bj":
            if kind == "fund":
                if adjust != "none":
                    raise ValueError("Sina ETF adapter supports unadjusted daily bars only")
                rows = client.fetch("fund_etf_hist_sina", symbol=prefix + pure)
            else:
                rows = client.fetch("stock_zh_a_daily", symbol=prefix + pure, adjust=adj, **dates)
        else:
            raise ValueError("Sina adapter supports equity daily bars")
    elif source == "eastmoney":
        if frequency in periods:
            if is_index:
                rows = client.fetch(
                    "index_zh_a_hist", symbol=pure, period=periods[frequency], **dates
                )
            elif kind == "fund":
                rows = client.fetch(
                    "fund_etf_hist_em", symbol=pure, period=periods[frequency], adjust=adj, **dates
                )
            else:
                rows = client.fetch(
                    "stock_zh_a_hist", symbol=pure, period=periods[frequency], adjust=adj, **dates
                )
        else:
            raise ValueError("unsupported bar frequency")
    else:
        raise ValueError("unsupported AKShare bars source")
    mapped = {}
    for row in rows:
        _validate_bar_identity(row, code, prefix, pure)
        row = {
            {
                "日期": "datetime",
                "时间": "datetime",
                "day": "datetime",
                "date": "datetime",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }.get(key, key): value
            for key, value in row.items()
        }
        if source == "eastmoney" and prefix not in ("hk", "us") and row.get("volume") is not None:
            row["volume"] = float(row["volume"]) * 100
        bar = normalize_bar(row, "akshare_" + source)
        bar.update(
            {
                "volume_unit": "share",
                "amount_unit": {"hk": "HKD", "us": "USD"}.get(prefix, "CNY"),
                "adapter": "akshare",
                "volume_coverage": "source period series",
            }
        )
        bar["datetime"] = str(bar["datetime"])[:10]
        mapped[bar["datetime"]] = bar
    return [mapped[key] for key in sorted(mapped)][-count:]


def profile(code):
    _, _, pure = require_a_share(code, "company profile")
    rows = client.fetch("stock_profile_cninfo", symbol=pure)
    if len(rows) != 1 or str(rows[0].get("A股代码")) != pure:
        raise ValueError("company profile identity mismatch")
    return rows[0]
