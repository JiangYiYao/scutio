"""行情领域入口：按市场、周期和口径选择数据源并校验覆盖。"""

from __future__ import annotations

from datetime import datetime, timezone

from scutio_data._providers import quotes
from scutio_data._providers.akshare import market as akshare_market
from scutio_data._providers.hithink import client as hithink
from scutio_data._providers.quote_parse import index_quote_rows
from scutio_data._providers.quotes import eastmoney_quote as eastmoney_quote
from scutio_data._providers.quotes import sina_quote as sina_quote
from scutio_data._providers.quotes import tencent_quote as tencent_quote
from scutio_data._runtime.parsing import finite_number
from scutio_data._runtime.symbols import market_of, require_security, validate_identity
from scutio_data._runtime.timeouts import operation

__all__ = [
    "security_quote",
    "security_bars",
    "QUOTE_FALLBACK_CHAIN",
    "BARS_FALLBACK_CHAIN",
    "QUOTE_FALLBACK_BY_MARKET",
    "BARS_FALLBACK_BY_MARKET",
]


# A 股默认链（历史别名；security_* 默认走 BY_MARKET）。
QUOTE_FALLBACK_CHAIN = ("tencent", "sina")


BARS_FALLBACK_CHAIN = ("akshare_sina", "akshare_tencent", "akshare_eastmoney")


# 按市场默认链（security_* 在 sources=None 时使用）。
# 美股日 K：腾讯免费 fqkline 常过短，故 bars 优先东财。
QUOTE_FALLBACK_BY_MARKET = {
    "a": ("tencent", "sina"),
    "hk": ("tencent", "sina", "eastmoney"),
    "us": ("tencent", "sina", "eastmoney"),
}


BARS_FALLBACK_BY_MARKET = {
    "a": ("akshare_sina", "akshare_tencent", "akshare_eastmoney"),
    "hk": ("akshare_eastmoney", "akshare_sina"),
    "us": ("akshare_eastmoney", "akshare_sina"),
}


_BAR_FREQUENCIES = frozenset(["D", "1D", "W", "1W", "M", "1M"])


def _market_key(prefix: str) -> str:
    if prefix == "hk":
        return "hk"
    if prefix == "us":
        return "us"
    return "a"


def _default_quote_chain(prefix: str):
    return QUOTE_FALLBACK_BY_MARKET.get(_market_key(prefix), QUOTE_FALLBACK_CHAIN)


def _default_bars_chain_for_code(code):
    try:
        m = market_of(code)
    except Exception:
        m = "a"
    return BARS_FALLBACK_BY_MARKET.get(m, BARS_FALLBACK_CHAIN)


@operation("query")
def security_quote(codes, sources=None):
    """实时报价有序 fallback。

    ``sources=None`` 时按**每只票的市场**选默认链；显式 ``sources=`` 则整批统一。
    返回 ``{ok, partial, error, errors, sources_used, quotes, missing}``。
    """
    if isinstance(codes, (str, bytes)):
        codes = [codes]
    codes = list(codes or [])
    errors = {}
    invalid = []
    sources_used = []
    attempted_sources = []
    merged = {}
    pending = []
    for code in codes:
        try:
            _, prefix, pure = require_security(code, "security_quote")
            pending.append((code, prefix + pure, pure, prefix))
        except Exception as exc:
            errors["parse:%s" % code] = str(exc)
            invalid.append(str(code))

    if not pending:
        return {
            "ok": False,
            "partial": False,
            "error": "no valid codes",
            "source": None,
            "warning": None,
            "errors": errors,
            "sources_used": [],
            "quotes": {},
            "missing": list(invalid),
            "invalid": list(invalid),
            "requested_count": len(codes),
            "returned_count": 0,
        }

    use_global = sources is not None
    global_chain = tuple(sources) if use_global else None
    remaining = list(pending)

    def _run_source(source, items):
        still = []
        if not items:
            return still
        attempted_sources.append(source)
        try:
            fetchers = {
                "tencent": quotes.tencent_quote,
                "sina": quotes.sina_quote,
                "eastmoney": quotes.eastmoney_quote,
                "hithink": hithink.quotes,
            }
            if source in fetchers:
                batch = fetchers[source]([c for c, _, _, _ in items])
                for orig, symbol, pure, prefix in items:
                    row = batch.get(symbol) or batch.get(pure)
                    try:
                        if not isinstance(row, dict) or not (
                            row.get("price") or row.get("last_close")
                        ):
                            raise ValueError("missing or invalid quote")
                        validate_identity(
                            {**({"symbol": symbol} if symbol in batch else {}), **row}, symbol
                        )
                    except ValueError as exc:
                        errors[source + ":" + symbol] = str(exc)
                        still.append((orig, symbol, pure, prefix))
                        continue
                    merged[symbol] = dict(row, source=source, code=pure, symbol=symbol)
                    merged[symbol].setdefault(
                        "retrieved_at", datetime.now(timezone.utc).isoformat()
                    )
                    merged[symbol].setdefault("data_as_of", None)
                    if source not in sources_used:
                        sources_used.append(source)
            else:
                errors[source] = "unknown quote source"
                still = list(items)
        except Exception as exc:
            errors[source] = str(exc)
            still = list(items)
        return still

    if use_global:
        for source in global_chain:
            if not remaining:
                break
            remaining = _run_source(source, remaining)
    else:
        by_m = {"a": [], "hk": [], "us": []}
        for item in remaining:
            by_m[_market_key(item[3])].append(item)
        still_all = []
        for mkt, items in by_m.items():
            chain = QUOTE_FALLBACK_BY_MARKET.get(mkt, QUOTE_FALLBACK_CHAIN)
            if mkt == "a" and any(hithink.preferred(item[0]) for item in items):
                chain = ("hithink",) + chain
            cur = list(items)
            for source in chain:
                if not cur:
                    break
                cur = _run_source(source, cur)
            still_all.extend(cur)
        remaining = still_all

    missing = list(invalid) + [symbol for _, symbol, _, _ in remaining]
    ok = bool(merged)
    row_warnings = [
        "%s: %s" % (symbol, row.get("warning") or "partial quote")
        for symbol, row in merged.items()
        if row.get("partial")
    ]
    partial = ok and (bool(missing) or bool(row_warnings))
    error = None
    warning = None
    if not ok:
        error = (
            "; ".join("%s: %s" % (k, v) for k, v in errors.items()) or "all quote sources failed"
        )
    elif partial:
        warning = "; ".join(
            (["missing symbols: %s" % ",".join(missing)] if missing else []) + row_warnings
        )
    return {
        "ok": ok,
        "partial": partial,
        "error": error,
        "source": (
            sources_used[0]
            if len(sources_used) == 1
            else ("multi_source" if sources_used else None)
        ),
        "warning": warning,
        "errors": errors,
        "sources_used": sources_used,
        "attempted_sources": attempted_sources,
        "fallback_reason": errors or None,
        "retrieved_at": max(
            (row.get("retrieved_at") or "" for row in merged.values()), default=None
        ),
        "quotes": index_quote_rows(merged),
        "missing": missing,
        "invalid": invalid,
        "requested_count": len(codes),
        "returned_count": len(pending) - len(remaining),
    }


def _bar_candidate_quality(bars, frequency="D", count=80):
    """Classify one provider's bar series before accepting a fallback leg.

    Small fixtures and newly listed securities may legitimately return fewer
    rows than requested, so sparse-but-contiguous data is ``partial`` rather
    than failed.  A tiny series spanning months or years is not a usable time
    series and must not be used to calculate one-day changes.
    """
    rows = [row for row in (bars or []) if isinstance(row, dict)]
    requested = max(0, int(count or 0))
    freq = str(frequency or "D")
    issues = []
    parsed = []
    invalid_ohlc = 0
    for row in rows:
        raw_dt = str(row.get("datetime") or "").strip()
        try:
            stamp = datetime.fromisoformat(raw_dt.replace("/", "-")[:19])
        except ValueError:
            stamp = None
        if stamp is not None:
            parsed.append(stamp)
        prices = [finite_number(row.get(field)) for field in ("open", "close", "high", "low")]
        if any(value is None for value in prices):
            invalid_ohlc += 1
            continue
        open_, close, high, low = prices
        if not (low <= open_ <= high and low <= close <= high):
            invalid_ohlc += 1

    if invalid_ohlc:
        issues.append("invalid_ohlc_rows:%s" % invalid_ohlc)
    if len(parsed) != len(rows):
        issues.append("invalid_datetime_rows:%s" % (len(rows) - len(parsed)))
    if len(parsed) >= 2 and any(b <= a for a, b in zip(parsed, parsed[1:])):
        issues.append("non_increasing_datetime")

    max_gap_days = None
    daily = freq in {"D", "1D"}
    if daily and len(parsed) >= 2:
        max_gap_days = max((b.date() - a.date()).days for a, b in zip(parsed, parsed[1:]))
        # Tencent's US fallback can return only the listing-era row plus the
        # latest quote-derived row.  This is non-empty but not a time series.
        if requested >= 20 and len(rows) <= 3 and max_gap_days > 31:
            issues.append("sparse_excessive_gap_days:%s" % max_gap_days)

    fatal = bool(issues)
    status = "invalid" if fatal else "partial" if requested and len(rows) < requested else "ok"
    return {
        "status": status,
        "requested_count": requested,
        "returned_count": len(rows),
        "max_gap_days": max_gap_days,
        "issues": issues,
    }


@operation("history")
def security_bars(
    code,
    frequency="D",
    count=80,
    index=None,
    sources=None,
    adjust="none",
):
    """K 线有序 fallback。默认不复权；链按市场选择（见 ``BARS_FALLBACK_BY_MARKET``）。"""
    chain = tuple(sources) if sources is not None else _default_bars_chain_for_code(code)
    _, prefix, pure = require_security(code, "security_bars")
    identity = {
        "symbol": prefix + pure,
        "code": pure,
        "currency": {"hk": "HKD", "us": "USD"}.get(prefix, "CNY"),
        "identity_provenance": "request",
    }
    count = int(count)
    if count < 1:
        raise ValueError("count must be positive")
    if str(frequency) not in _BAR_FREQUENCIES:
        raise ValueError("unsupported bar frequency")
    adjust = {"forward": "qfq", "1": "qfq", "backward": "hfq", "2": "hfq", "0": "none"}.get(
        str(adjust), str(adjust)
    )
    if adjust not in ("none", "qfq", "hfq"):
        raise ValueError("adjust must be none, qfq or hfq")
    if sources is None and market_of(code) == "a":
        if adjust == "qfq" and frequency in ("D", "1D"):
            chain = ("akshare_eastmoney", "akshare_sina")
        elif adjust == "hfq":
            chain = ("akshare_sina", "akshare_eastmoney")
    if sources is None and frequency in ("D", "1D") and not index and hithink.preferred(code):
        chain = ("hithink",) + chain
    errors = {}
    attempted = []
    for source in chain:
        attempted.append(source)
        try:
            if source == "hithink":
                bars = hithink.bars(
                    code, frequency=frequency, count=count, index=index, adjust=adjust
                )
            elif source in ("akshare_sina", "akshare_eastmoney", "akshare_tencent"):
                bars = akshare_market.bars(
                    code,
                    frequency=frequency,
                    count=count,
                    index=index,
                    adjust=adjust,
                    source=source.removeprefix("akshare_"),
                )
            else:
                errors[source] = "unknown bars source"
                continue
            if bars:
                for bar in bars:
                    validate_identity(bar, code, required=False)
                quality = _bar_candidate_quality(bars, frequency=frequency, count=count)
                if quality["status"] == "invalid":
                    errors[source] = "bar quality failed: %s" % ", ".join(quality["issues"])
                    continue
                return {
                    **identity,
                    "ok": True,
                    "error": None,
                    "errors": errors,
                    "source": source,
                    "sources_used": [source],
                    "attempted_sources": attempted,
                    "fallback_reason": errors or None,
                    "data_as_of": bars[-1].get("data_as_of"),
                    "retrieved_at": bars[-1].get("retrieved_at")
                    or datetime.now(timezone.utc).isoformat(),
                    "coverage": {
                        "amount": all(b.get("amount") is not None for b in bars),
                        "volume_unit": "share",
                    },
                    "bars": bars,
                    "adjust": str(adjust or "none"),
                    "adjustment_basis": {
                        "provider": source,
                        "direction": adjust,
                        "cross_source_equivalent": adjust == "none",
                        "method": "source_native" if adjust != "none" else "unadjusted",
                    },
                    "warning": "Provider-native adjustment; do not splice or compare adjusted prices across sources"
                    if adjust != "none"
                    else None,
                    "frequency": str(frequency),
                    "partial": quality["status"] == "partial",
                    "quality": quality,
                }
            errors[source] = "empty bars"
        except Exception as exc:
            errors[source] = str(exc)
    return {
        **identity,
        "ok": False,
        "error": "; ".join("%s: %s" % (k, v) for k, v in errors.items())
        or "all bars sources failed",
        "errors": errors,
        "source": None,
        "sources_used": [],
        "attempted_sources": attempted,
        "fallback_reason": errors or None,
        "bars": [],
        "adjust": str(adjust or "none"),
        "frequency": str(frequency),
    }
