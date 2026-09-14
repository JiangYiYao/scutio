"""估值：报价侧快照、A 股历史估值与纯公式工具。

一致预期原始表在 ``research.eps_forecast``；本模块**不再**拉取一致预期。
需要前向 PE/PEG 时：自行取 ``eps_forecast`` 再调 ``forward_pe`` / ``calc_peg``。"""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, datetime, timedelta

from scutio_data._runtime.results import result_err, result_ok
from scutio_data._runtime.symbols import require_a_share, split_code
from scutio_data._runtime.timeouts import operation
from scutio_data.market import security_quote

__all__ = [
    "forward_pe",
    "pe_digestion",
    "calc_peg",
    "valuation_snapshot",
    "valuation_history",
]


_HISTORY_METRICS = ("pe_ttm", "pe_static", "pb", "pcf", "ps")


_AK_VALUE_MAP = {
    "数据日期": "date",
    "当日收盘价": "close",
    "当日涨跌幅": "change_pct",
    "总市值": "total_mcap",
    "流通市值": "float_mcap",
    "总股本": "total_shares",
    "流通股本": "float_shares",
    "PE(TTM)": "pe_ttm",
    "PE(静)": "pe_static",
    "市净率": "pb",
    "PEG值": "peg",
    "市现率": "pcf",
    "市销率": "ps",
}


_BAIDU_INDICATORS = {
    "市盈率(TTM)": "pe_ttm",
    "市净率": "pb",
}


def _float_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_text(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None
    text = text[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _eastmoney_history(code):
    from scutio_data._providers.akshare.client import fetch

    raw_rows = fetch("stock_value_em", symbol=code)
    rows = []
    for raw in raw_rows:
        day = _date_text(raw.get("数据日期"))
        if not day:
            continue
        if not set(_AK_VALUE_MAP).issubset(raw):
            raise ValueError("AKShare valuation schema changed")
        row = {"date": day}
        row.update(
            {
                target: _float_or_none(raw.get(source))
                for source, target in _AK_VALUE_MAP.items()
                if target != "date"
            }
        )
        rows.append(row)
    rows.sort(key=lambda item: item["date"])
    if not rows:
        raise ValueError("empty Eastmoney valuation history")
    return rows


def _baidu_indicator_history(code, indicator):
    from scutio_data._providers.akshare.client import fetch

    raw_rows = fetch("stock_zh_valuation_baidu", symbol=code, indicator=indicator, period="全部")
    rows = []
    for raw in raw_rows:
        day, value = _date_text(raw.get("date")), _float_or_none(raw.get("value"))
        if day and value is not None:
            rows.append((day, value))
    if not rows:
        raise ValueError("empty Baidu valuation history for %s" % indicator)
    return rows


def _baidu_history(code):
    by_date = {}
    errors = {}
    for indicator, metric in _BAIDU_INDICATORS.items():
        try:
            for day, value in _baidu_indicator_history(code, indicator):
                by_date.setdefault(day, {"date": day})[metric] = value
        except Exception as exc:
            errors[metric] = str(exc)
    rows = [by_date[key] for key in sorted(by_date)]
    if not rows:
        raise ValueError("; ".join("%s: %s" % item for item in errors.items()))
    return rows, errors


def _quantile(sorted_values, fraction):
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _metric_summary(rows, metric):
    dated = [
        (row["date"], row.get(metric))
        for row in rows
        if _float_or_none(row.get(metric)) is not None and float(row.get(metric)) > 0
    ]
    if not dated:
        return None
    current_date, current = dated[-1]
    values = sorted(float(value) for _, value in dated)
    return {
        "current": current,
        "current_date": current_date,
        "sample_count": len(values),
        "percentile": bisect_right(values, current) / len(values),
        "min": values[0],
        "p25": _quantile(values, 0.25),
        "median": _quantile(values, 0.5),
        "p75": _quantile(values, 0.75),
        "max": values[-1],
    }


def _history_summary(rows):
    end = date.fromisoformat(rows[-1]["date"])
    windows = {}
    for label, years in (("3y", 3), ("5y", 5), ("all", None)):
        cutoff = end - timedelta(days=round(years * 365.25)) if years else None
        selected = [
            row for row in rows if cutoff is None or date.fromisoformat(row["date"]) >= cutoff
        ]
        metrics = {}
        for metric in _HISTORY_METRICS:
            summary = _metric_summary(selected, metric)
            if summary:
                metrics[metric] = summary
        windows[label] = {
            "start_date": selected[0]["date"],
            "end_date": selected[-1]["date"],
            "sample_count": len(selected),
            "metrics": metrics,
        }
    return windows


@operation("history")
def valuation_history(code, include_series=True):
    """A 股历史估值与 3年/5年/可得全期分位；东财失败才回退百度。

    分位只使用有限正值；避免亏损期负 PE/PCF 进入排序。``include_series=False``
    只返回紧凑摘要，供角色包等低上下文场景使用。
    """
    try:
        _, _, pure = require_a_share(code, "valuation_history")
    except Exception as exc:
        return result_err(exc, source="valuation_history", code=str(code))

    errors = {}
    partial = False
    try:
        rows = _eastmoney_history(pure)
        source = "eastmoney_value_analysis"
    except Exception as exc:
        errors["eastmoney"] = str(exc)
        try:
            rows, baidu_errors = _baidu_history(pure)
            errors.update({"baidu_%s" % key: value for key, value in baidu_errors.items()})
            source = "baidu_valuation_backup"
            partial = True
        except Exception as backup_exc:
            errors["baidu"] = str(backup_exc)
            return result_err(
                "valuation history unavailable",
                source="eastmoney_value_analysis->baidu_valuation_backup",
                code=str(code),
                errors=errors,
                items=[] if include_series else None,
            )

    return result_ok(
        source=source,
        adapter="akshare",
        code=str(code),
        market="a",
        start_date=rows[0]["date"],
        end_date=rows[-1]["date"],
        sample_count=len(rows),
        value_policy="finite_positive_only",
        windows=_history_summary(rows),
        series_included=bool(include_series),
        items=rows if include_series else None,
        partial=partial or (source == "eastmoney_value_analysis" and len(rows) >= 5000),
        source_row_limit=5000 if source == "eastmoney_value_analysis" else None,
        errors=errors,
    )


def forward_pe(price, eps):
    """前向 PE = 价格 / 预期 EPS。``eps`` 由调用方提供（如一致预期）。"""
    return float("inf") if eps <= 0 else price / eps


def pe_digestion(current_pe, cagr, target_pe=30):
    """消化高 PE 所需年数（对数模型）；cagr 为小数，例如 0.20 表示 20%。"""
    if current_pe <= target_pe:
        return 0.0
    return float("inf") if cagr <= 0 else math.log(current_pe / target_pe) / math.log(1 + cagr)


def calc_peg(pe, cagr):
    """PEG：市盈率除以增长率百分数；cagr 输入小数，例如 0.15 表示 15%。"""
    return float("inf") if cagr <= 0 else pe / (cagr * 100)


def _row_from_security_quote(env, code):
    """从 ``security_quote`` 信封取出单票 row。"""
    quotes = (env or {}).get("quotes") or {}
    if not isinstance(quotes, dict) or not quotes:
        return None
    try:
        prefix, pure = split_code(code)
    except ValueError:
        return None
    symbol = prefix + pure
    for key, row in quotes.items():
        if not isinstance(row, dict) or row.get("price") is None:
            continue
        try:
            key_symbol = "".join(split_code(key)) if not str(key).isdigit() else None
            row_symbol = "".join(split_code(row["symbol"])) if row.get("symbol") else None
            if row.get("exchange") and row.get("code"):
                exchange_symbol = "".join(split_code(str(row["exchange"]) + str(row["code"])))
                if row_symbol and row_symbol != exchange_symbol:
                    continue
                row_symbol = exchange_symbol
            if key_symbol and key_symbol != symbol:
                continue
            if row_symbol and row_symbol != symbol:
                continue
            if row.get("code") and str(row["code"]) != pure:
                continue
            if key_symbol == symbol or row_symbol == symbol:
                return row
        except (TypeError, ValueError):
            continue
    return None


def _quote_valuation_snapshot(code, quote_env=None):
    """报价侧估值快照，返回 result_ok / result_err，不抛异常。

    仅 ``security_quote``：现价、市值、PE-TTM、PB 等。
    **不含**一致预期 / 前向 PE / PEG（见 ``research.eps_forecast`` + 本模块公式）。
    调用方已经取过报价时传 ``quote_env``，避免重复请求。
    报价失败 → ``ok=False``。
    """
    env = quote_env
    if env is None:
        try:
            env = security_quote([code])
        except Exception as exc:
            return result_err(exc, source="security_quote", code=str(code))

    if not isinstance(env, dict):
        return result_err("invalid quote envelope", source="security_quote", code=str(code))
    if not env.get("ok"):
        return result_err(
            (env or {}).get("error") or "quote_failed",
            source="security_quote",
            code=str(code),
            errors=(env or {}).get("errors"),
            missing=(env or {}).get("missing"),
        )

    q = _row_from_security_quote(env, code)
    if q is None:
        return result_err(
            "quote_missing",
            source="security_quote",
            code=str(code),
            quotes=env.get("quotes"),
            missing=env.get("missing"),
        )

    quote_src = q.get("source") or ((env.get("sources_used") or [None])[0]) or "quote"

    return result_ok(
        source=quote_src,
        quote_source=quote_src,
        name=q.get("name"),
        price=q.get("price"),
        mcap_yi=q.get("mcap_yi"),
        pe_ttm=q.get("pe_ttm"),
        pb=q.get("pb"),
        change_pct=q.get("change_pct"),
        last_close=q.get("last_close"),
        currency=q.get("currency"),
        code=str(code),
        symbol="".join(split_code(code)),
        retrieved_at=q.get("retrieved_at")
        or (env.get("retrieved_at") if len(env["quotes"]) == 1 else None),
        data_as_of=q.get("data_as_of"),
        time=q.get("time"),
        provider_timestamp=q.get("provider_timestamp"),
        partial=bool(q.get("partial")),
        warning=q.get("warning"),
    )


@operation("query")
def valuation_snapshot(code, quote_env=None, *, sources=None):
    """Independent valuation metrics, preserving provenance of reused quote fields."""
    from datetime import datetime, timezone

    from scutio_data._providers.hithink import client as hithink

    errors = {}
    metrics = None
    chain = (
        tuple(sources)
        if sources is not None
        else (("hithink", "quote") if hithink.preferred(code) else ("quote",))
    )
    for source in chain:
        if source == "hithink":
            try:
                metrics = hithink.valuation(code)
                break
            except Exception as exc:
                errors[source] = str(exc)
        elif source != "quote":
            errors[source] = "unsupported valuation source"
    if metrics is None and "quote" not in chain:
        return result_err(
            "; ".join(errors.values()) or "no valuation sources",
            source="valuation_snapshot",
            errors=errors,
        )
    # A supplied quote is reused, including a supplied failure; no hidden re-fetch.
    if sources is not None and "quote" not in chain and quote_env is None:
        quote_env = security_quote([code], sources=("hithink",))
    quote = _quote_valuation_snapshot(code, quote_env=quote_env)
    stamp = datetime.now(timezone.utc).isoformat()
    quote_time = {
        key: quote.get(key) for key in ("retrieved_at", "data_as_of", "time", "provider_timestamp")
    }
    quote_fields = ("price", "name", "mcap_yi", "change_pct", "last_close", "pe_ttm", "pb")
    quote["field_timestamps"] = {key: dict(quote_time) for key in quote_fields}
    quote["input_quote_retrieved_at"] = quote.get("retrieved_at")
    if metrics is None:
        if (
            quote.get("ok")
            and quote.get("pe_ttm") is None
            and quote.get("pb") is None
            and "quote" in chain
        ):
            free_env = security_quote([code], sources=("tencent", "sina", "eastmoney"))
            free = _quote_valuation_snapshot(code, quote_env=free_env)
            if free.get("ok"):
                quote.update(
                    pe_ttm=free.get("pe_ttm"),
                    pb=free.get("pb"),
                    field_sources={
                        "price": quote.get("quote_source"),
                        "pe_ttm": free.get("source"),
                        "pb": free.get("source"),
                    },
                )
                quote["source"] = free.get("source")
                for field in ("pe_ttm", "pb"):
                    quote["field_timestamps"][field] = {
                        key: free.get(key) for key in ("retrieved_at", "data_as_of", "time")
                    }
        if quote.get("ok") and quote.get("pe_ttm") is None and quote.get("pb") is None:
            quote.update(partial=True, warning="valuation metrics unavailable")
        quote.update(fallback_reason=errors or None, computed_at=stamp)
        return quote
    result = (
        dict(quote)
        if quote.get("ok")
        else {"code": str(code), "partial": True, "warning": "quote fields unavailable"}
    )
    quote_source = quote.get("source") if quote.get("ok") else None
    result.update(metrics)
    result.update(
        ok=True,
        error=None,
        source="hithink",
        quote_source=quote_source,
        field_sources={
            **{
                key: quote_source
                for key in ("price", "name", "mcap_yi", "change_pct", "last_close")
            },
            **{key: "hithink" for key in ("pe_ttm", "pe_mrq", "pb", "pb_mrq", "ps_ttm", "pcf_ttm")},
        },
        sources_used=list(dict.fromkeys(src for src in ("hithink", quote_source) if src)),
        fallback_reason=errors or None,
    )
    result["computed_at"] = stamp
    result["input_quote_retrieved_at"] = quote.get("retrieved_at")
    metric_time = {
        key: metrics.get(key) for key in ("retrieved_at", "data_as_of", "provider_timestamp")
    }
    result["field_timestamps"] = {
        **quote["field_timestamps"],
        **{
            key: dict(metric_time)
            for key in ("pe_ttm", "pe_mrq", "pb", "pb_mrq", "ps_ttm", "pcf_ttm")
        },
    }
    result["missing_fields"] = [
        key
        for key in ("price", "mcap_yi", "pe_ttm", "pe_mrq", "pb", "ps_ttm", "pcf_ttm")
        if result.get(key) is None
    ]
    result["partial"] = bool(result["missing_fields"]) or bool(quote.get("partial"))
    return result
