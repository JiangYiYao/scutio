"""中美宏观数据与聚合快照的公开入口。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any, Dict, Optional, Sequence

from scutio_data._runtime.environment import CN_TZ
from scutio_data._runtime.parsing import finite_number
from scutio_data._runtime.results import (
    envelope_items,
    result_err,
    result_list,
    result_list_err,
    result_ok,
)
from scutio_data._runtime.symbols import canonical_symbol, split_code
from scutio_data._runtime.timeouts import operation
from scutio_data.batch import fetch_many
from scutio_data.macro import quotes, series
from scutio_data.macro.quotes import commodities_spot, fx_usdcny
from scutio_data.macro.series import (
    _DEFAULT_SURPRISE_SERIES,
    _SHIBOR_SNAPSHOT_KEYS,
    _SHIBOR_TENORS,
    _date_str,
    cn_macro_series,
    macro_series,
    map_lpr_rows,
    us_macro_series,
)
from scutio_data.macro.series import ALL_MACRO_SERIES as ALL_MACRO_SERIES
from scutio_data.macro.series import MACRO_SERIES as MACRO_SERIES
from scutio_data.macro.series import US_MACRO_SERIES as US_MACRO_SERIES
from scutio_data.macro.series import list_macro_series as list_macro_series
from scutio_data.macro.series import map_us_indicator_rows as map_us_indicator_rows

__all__ = [
    "MACRO_SERIES",
    "US_MACRO_SERIES",
    "ALL_MACRO_SERIES",
    "list_macro_series",
    "lpr_history",
    "rates_snapshot",
    "bond_yields_cn_us",
    "cn_macro_series",
    "us_macro_series",
    "macro_series",
    "fx_usdcny",
    "commodities_spot",
    "index_board",
    "macro_snapshot",
    "economic_calendar",
    "macro_surprises",
]


def _surprise_item(row: dict) -> dict:
    item = dict(row or {})
    actual = finite_number(item.get("value") if "value" in item else item.get("actual"))
    forecast = finite_number(item.get("forecast"))
    surprise = actual - forecast if actual is not None and forecast is not None else None
    item["actual"] = actual
    item["forecast"] = forecast
    item["surprise"] = round(surprise, 6) if surprise is not None else None
    item["surprise_pct"] = (
        round(surprise / abs(forecast) * 100, 6)
        if surprise is not None and forecast not in (None, 0)
        else None
    )
    return item


@operation("batch")
def economic_calendar(day=None, regions=None, min_importance=0, days=1) -> dict:
    """全球宏观发布日期/实际/预期/前值；可取从 ``day`` 起的短窗口。

    当前使用百度财经日历聚合源；官方机构只发布日程与实际值，不发布市场一致预期，
    因此本函数不伪造“官方预期 backup”。需要历史同指标惊喜时用
    :func:`macro_surprises`（金十主、现有命名序列 actual-only 备）。
    """
    try:
        from scutio_data._providers.akshare.economic_calendar import CalendarError, calendar_rows
        from scutio_data._runtime.dates import date_arg

        start_day = date_arg(day, default=datetime.now(CN_TZ).date())
        span = max(1, min(int(days or 1), 14))
        region_set = {
            str(value).strip().lower()
            for value in ([regions] if isinstance(regions, str) else (regions or []))
            if str(value).strip()
        }
        end_day = start_day + timedelta(days=span - 1)
        errors, completed_days = {}, span
        try:
            raw = calendar_rows(start_day.isoformat(), "economic_data", end_day=end_day.isoformat())
        except CalendarError as exc:
            if not exc.completed_days:
                raise
            raw, completed_days = exc.items, exc.completed_days
            errors[exc.failed_date] = str(exc)
        out = []
        for item in raw:
            importance = finite_number(item.get("importance"))
            if importance is not None and importance < float(min_importance or 0):
                continue
            hay = "%s %s" % (item.get("country") or "", item.get("region") or "")
            if region_set and not any(token in hay.lower() for token in region_set):
                continue
            normalized = _surprise_item(item)
            normalized["previous"] = finite_number(item.get("previous"))
            out.append(normalized)
        out.sort(key=lambda row: (row.get("date") or "", row.get("time") or ""))
        return result_list(
            out,
            source="baidu_finance_calendar",
            date=start_day.isoformat(),
            start_date=start_day.isoformat(),
            end_date=end_day.isoformat(),
            days=span,
            regions=sorted(region_set),
            min_importance=float(min_importance or 0),
            partial=bool(errors),
            errors=errors,
            completed_days=completed_days,
            backup_used=False,
            note="预期值来自聚合调查口径；不是统计机构官方预测",
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="economic_calendar",
            note="财经日历预期值无同口径官方 backup；失败不得用官方实际值冒充预期",
        )


def _surprise_series(name, limit, fallback):
    primary_error = None
    if name not in series.CONSENSUS_SPECS:
        primary_error = "no jin10 consensus mapping"
    else:
        try:
            rows = series.consensus_history(name, limit=max(1, int(limit)))
            return result_list(
                [dict(_surprise_item(row), series=name) for row in rows],
                partial=any(row.get("partial") or row.get("stale") for row in rows),
            )
        except Exception as exc:
            primary_error = str(exc)
    if not fallback:
        return result_list_err(primary_error)
    env = series.macro_series(name, limit=limit)
    if not env.get("ok"):
        return result_list_err("%s; fallback: %s" % (primary_error, env.get("error")))
    items = [
        dict(_surprise_item(row), series=name, data_quality="actual_only_fallback")
        for row in envelope_items(env)
    ]
    if name in series.CONSENSUS_SPECS:
        for item in items:
            item["primary_error"] = primary_error
    return result_list(items, partial=True, primary_error=primary_error)


@operation("batch")
def macro_surprises(names=None, limit=12, fallback=False) -> dict:
    """中美实际-预期历史；fallback 仅补 actual，不把前值当预期。"""
    selected = list(names or _DEFAULT_SURPRISE_SERIES)
    fetched = fetch_many(
        {name: partial(_surprise_series, name, limit, fallback) for name in selected}
    )["results"]
    items, errors, partial_result = [], {}, False
    for name in selected:
        env = fetched[name]["result"]
        items.extend(envelope_items(env))
        error = env.get("primary_error") or env.get("error")
        if error:
            errors[name] = error
        partial_result = partial_result or bool(error) or bool(env.get("partial"))
    items.sort(key=lambda row: (row.get("date") or "", row.get("series") or ""), reverse=True)
    if not items:
        return result_list_err(
            "; ".join("%s: %s" % pair for pair in errors.items()) or "no macro surprises",
            source="macro_surprises",
            errors=errors,
            selected=selected,
        )
    return result_list(
        items,
        source="macro_surprises",
        partial=partial_result,
        errors=errors,
        selected=selected,
        primary_source="jin10_ec",
        fallback_source="macro_series_actual_only" if fallback else None,
        note="surprise=actual-forecast；actual-only fallback 的 surprise 为 None",
    )


@operation("history")
def lpr_history(limit=20):
    from scutio_data._providers.akshare.client import fetch

    try:
        rows = sorted(
            fetch("macro_china_lpr"), key=lambda row: str(row.get("TRADE_DATE") or ""), reverse=True
        )
        items = map_lpr_rows(rows, limit=limit)
        return (
            result_list(items, source="akshare_lpr")
            if items
            else result_list_err("lpr empty", source="akshare_lpr")
        )
    except Exception as exc:
        return result_list_err(str(exc), source="akshare_lpr")


def _shibor_latest(key):
    from scutio_data._providers.akshare.client import fetch
    from scutio_data._providers.akshare.errors import AKShareError

    tenor = _SHIBOR_TENORS[key]
    try:
        rows = fetch(
            "rate_interbank", market="上海银行同业拆借市场", symbol="Shibor人民币", indicator=tenor
        )
    except AKShareError as exc:
        return result_err(str(exc), source="akshare_rate_interbank", error_code=exc.code)
    rows.sort(key=lambda row: str(row.get("报告日") or ""), reverse=True)
    if not rows:
        return result_err("empty", source="akshare_rate_interbank")
    row = rows[0]
    return result_ok(
        source="akshare_rate_interbank",
        item={
            "date": _date_str(row.get("报告日")),
            "period": tenor,
            "rate": finite_number(row.get("利率")),
            "change": finite_number(row.get("涨跌")),
            "units": {"rate": "pct", "change": "bp"},
            "source": "akshare_rate_interbank",
        },
    )


@operation("batch")
def rates_snapshot() -> dict:
    """LPR 最新 + SHIBOR 多期限快照（默认 ON/1W/1M/3M/1Y）。"""
    jobs = {"lpr": partial(lpr_history, limit=3)}
    jobs.update({key: partial(_shibor_latest, key) for key in _SHIBOR_SNAPSHOT_KEYS})
    results = fetch_many(jobs)["results"]
    legs, errors, shibor = {}, {}, {}
    lpr = results["lpr"]["result"]
    lpr_items = envelope_items(lpr)
    if lpr.get("ok") and lpr_items:
        legs["lpr"] = lpr_items[0]
    else:
        errors["lpr"] = lpr.get("error") or "lpr failed"
    for key in _SHIBOR_SNAPSHOT_KEYS:
        env = results[key]["result"]
        if env.get("ok"):
            shibor[key] = env["item"]
        else:
            errors["shibor_%s" % key] = env.get("error") or "shibor failed"
    if shibor:
        legs["shibor"] = shibor
    if not legs:
        return result_err(
            "all rate legs failed: %s" % errors, source="rates_snapshot", errors=errors
        )
    return result_ok(source="rates_snapshot", partial=bool(errors), errors=errors or None, **legs)


@operation("history")
def bond_yields_cn_us(limit=30):
    from scutio_data._providers.akshare.client import fetch

    try:
        rows = fetch("bond_zh_us_rate")
        fields = {
            "cn_2y": "中国国债收益率2年",
            "cn_5y": "中国国债收益率5年",
            "cn_10y": "中国国债收益率10年",
            "cn_30y": "中国国债收益率30年",
            "cn_10y_2y": "中国国债收益率10年-2年",
            "us_2y": "美国国债收益率2年",
            "us_5y": "美国国债收益率5年",
            "us_10y": "美国国债收益率10年",
            "us_30y": "美国国债收益率30年",
            "us_10y_2y": "美国国债收益率10年-2年",
        }
        items = [
            {
                "date": _date_str(row.get("日期")),
                "unit": "pct",
                "freq": "D",
                "source": "akshare_bond_zh_us_rate",
                **{key: finite_number(row.get(col)) for key, col in fields.items()},
            }
            for row in rows
        ]
        items.sort(key=lambda row: row["date"], reverse=True)
        return (
            result_list(items[: max(1, int(limit))], source="akshare_bond_zh_us_rate")
            if items
            else result_list_err("bond yields empty", source="akshare_bond_zh_us_rate")
        )
    except Exception as exc:
        return result_list_err(str(exc), source="akshare_bond_zh_us_rate")


@operation("query")
def index_board(codes: Optional[Sequence[str]] = None) -> dict:
    """指数报价板；复用公共报价门面，按完整证券身份回退与报告缺项。"""
    from scutio_data.market import security_quote

    default = ["sh000001", "sz399001", "sz399006", "sh000300", "sh000905", "sh000688"]
    use = default if codes is None else [codes] if isinstance(codes, str) else list(codes)
    requested, invalid, errors = [], [], {}
    for code in use:
        try:
            symbol = canonical_symbol(code)
            if symbol not in requested:
                requested.append(symbol)
        except ValueError as exc:
            invalid.append(str(code))
            errors["parse:%s" % code] = str(exc)
    try:
        env = security_quote(requested)
    except Exception as exc:
        env = result_err(exc, source="security_quote")
    errors.update(env.get("errors") or {})
    quoted = (env.get("quotes") or {}) if env.get("ok") else {}
    retrieved_at = datetime.now(timezone.utc).isoformat()
    board, missing = [], list(invalid)
    for symbol in requested:
        row = quoted.get(symbol)
        if (
            not isinstance(row, dict)
            or row.get("symbol") != symbol
            or row.get("code") != split_code(symbol)[1]
        ):
            missing.append(symbol)
            if row is not None:
                errors["identity:%s" % symbol] = "quote security identity mismatch"
            continue
        board.append(
            {
                "code": symbol,
                "name": row.get("name"),
                "price": row.get("price"),
                "change_pct": row.get("change_pct"),
                "open": row.get("open"),
                "last_close": row.get("last_close"),
                "time": row.get("time"),
                "data_as_of": row.get("data_as_of"),
                "retrieved_at": row.get("retrieved_at") or retrieved_at,
                "source": row.get("source"),
                "partial": bool(row.get("partial")),
                "warning": row.get("warning"),
            }
        )
    partial = bool(board) and (
        bool(missing) or bool(env.get("partial")) or any(row["partial"] for row in board)
    )
    warning = env.get("warning")
    if missing:
        warning = "; ".join(filter(None, (warning, "missing symbols: %s" % ",".join(missing))))
    metadata = {
        "indices": board,
        "partial": partial,
        "warning": warning,
        "errors": errors,
        "missing": missing,
        "invalid": invalid,
        "requested_count": len(requested) + len(invalid),
        "returned_count": len(board),
        "coverage": {
            "requested_codes": requested,
            "returned_codes": [row["code"] for row in board],
            "missing_codes": missing,
        },
        "sources_used": env.get("sources_used") or [],
        "attempted_sources": env.get("attempted_sources") or [],
        "fallback_reason": env.get("fallback_reason"),
    }
    source = env.get("source") or "security_quote"
    if not board:
        return result_err(env.get("error") or "no valid index quotes", source=source, **metadata)
    return result_ok(source=source, **metadata)


@operation("batch")
def macro_snapshot(preloaded=None) -> dict:
    """聚合 P0 快照；可传已取过的各腿信封，避免与单项 API 重复请求。

    ``preloaded`` 键：``rates_snapshot`` / ``bond_yields_cn_us`` /
    ``fx_usdcny`` / ``index_board`` / ``commodities_spot`` / ``series``。
    ``series`` 为 ``{series_name: envelope}``。
    """
    errors: Dict[str, str] = {}
    payload: Dict[str, Any] = {}
    try:
        supplied = dict(preloaded or {})
        supplied_series = dict(supplied.get("series") or {})
    except (TypeError, ValueError) as exc:
        return result_err(
            "invalid preloaded mapping: %s" % exc,
            source="macro_snapshot",
            error_code="invalid_preloaded",
        )
    cn_names = ("cpi_yoy", "pmi_mfg", "pmi_non_mfg", "forex_reserves", "rrr", "social_financing")
    us_names = ("us_cpi_yoy", "us_unemployment", "us_nfp", "us_ism_pmi", "us_fed_funds_upper")
    loaders = {
        "rates_snapshot": rates_snapshot,
        "bond_yields_cn_us": partial(bond_yields_cn_us, limit=3),
        "fx_usdcny": quotes.fx_usdcny,
        "index_board": index_board,
        "commodities_spot": quotes.commodities_spot,
    }
    jobs = {name: loader for name, loader in loaders.items() if name not in supplied}
    jobs.update(
        {
            "series:" + name: partial(series.cn_macro_series, name, limit=1)
            for name in cn_names
            if name not in supplied_series
        }
    )
    jobs.update(
        {
            "series:" + name: partial(series.us_macro_series, name, limit=1)
            for name in us_names
            if name not in supplied_series
        }
    )
    fetched = fetch_many(jobs)["results"]
    for name in loaders:
        if name not in supplied:
            supplied[name] = fetched[name]["result"]
        elif not isinstance(supplied[name], dict):
            supplied[name] = result_err(
                "invalid preloaded envelope for " + name, source="macro_snapshot"
            )
    for name in (*cn_names, *us_names):
        if name not in supplied_series:
            supplied_series[name] = fetched["series:" + name]["result"]
        elif not isinstance(supplied_series[name], dict):
            supplied_series[name] = result_err(
                "invalid preloaded envelope for " + name, source="macro_snapshot"
            )

    rates = supplied["rates_snapshot"]
    if rates.get("ok"):
        payload["rates"] = {k: rates[k] for k in ("lpr", "shibor") if k in rates}
        if rates.get("errors"):
            errors["rates_partial"] = str(rates.get("errors"))
    else:
        errors["rates"] = rates.get("error") or "rates failed"

    bonds = supplied["bond_yields_cn_us"]
    bonds_items = envelope_items(bonds)
    if bonds.get("ok") and bonds_items:
        payload["bonds"] = bonds_items[0]
    else:
        errors["bonds"] = bonds.get("error") or "bonds failed"

    fx = supplied["fx_usdcny"]
    if fx.get("ok"):
        payload["fx"] = {
            "pair": fx.get("pair"),
            "price": fx.get("price"),
            "name": fx.get("name"),
            "time": fx.get("time"),
            "date": fx.get("date"),
            "units": fx.get("units"),
            "source": fx.get("source"),
        }
    else:
        errors["fx"] = fx.get("error") or "fx failed"

    idx = supplied["index_board"]
    if idx.get("ok"):
        payload["indices"] = idx.get("indices")
        payload["indices_status"] = {
            key: idx.get(key)
            for key in (
                "source",
                "partial",
                "missing",
                "invalid",
                "coverage",
                "errors",
                "sources_used",
                "attempted_sources",
                "fallback_reason",
                "warning",
                "requested_count",
                "returned_count",
            )
        }
        if idx.get("partial") or idx.get("missing"):
            errors["indices_partial"] = idx.get("warning") or "partial index quote coverage"
    else:
        errors["indices"] = idx.get("error") or "indices failed"

    cmd = supplied["commodities_spot"]
    if cmd.get("ok"):
        payload["commodities"] = {k: cmd[k] for k in ("gold", "wti") if cmd.get(k)}
        if cmd.get("errors"):
            errors["commodities_partial"] = str(cmd.get("errors"))
    else:
        errors["commodities"] = cmd.get("error") or "commodities failed"

    for sname in (*cn_names, *us_names):
        ser = supplied_series[sname]
        items = envelope_items(ser)
        if ser.get("ok") and items and items[0].get("value") is not None:
            payload.setdefault("latest_series", {})[sname] = items[0]
            if ser.get("partial") or ser.get("stale"):
                errors["series_%s" % sname] = "partial or stale source coverage"
        else:
            errors["series_%s" % sname] = ser.get("error") or "empty"

    if not payload:
        return result_err(
            "macro_snapshot all legs failed: %s" % errors,
            source="macro_snapshot",
            errors=errors,
        )
    return result_ok(
        source="macro_snapshot",
        partial=bool(errors),
        errors=errors or None,
        **payload,
    )
