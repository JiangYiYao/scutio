"""中美宏观数据与聚合快照的公开入口。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

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


@operation("batch")
def macro_surprises(names=None, limit=12, fallback=False) -> dict:
    """中美关键指标实际-预期惊喜历史。

    金十 ``[日期, 今值, 预测值, 前值]`` 为主。显式 ``fallback=True`` 时可退到 toolkit 已有
    命名序列，但该备源通常只有 actual/prev，显式标记 ``actual_only_fallback``，
    不会把 ``prev`` 冒充 ``forecast``。
    """
    selected = list(names or _DEFAULT_SURPRISE_SERIES)
    items: List[dict] = []
    errors: Dict[str, str] = {}
    partial = False
    for name in selected:
        if name not in series.CONSENSUS_SPECS:
            errors[name] = "no jin10 consensus mapping"
            partial = True
            if not fallback:
                continue
            env = series.macro_series(name, limit=limit)
            if env.get("ok"):
                for row in envelope_items(env):
                    item = _surprise_item(row)
                    item["series"] = name
                    item["data_quality"] = "actual_only_fallback"
                    items.append(item)
            continue
        try:
            rows = series.consensus_history(name, limit=max(1, int(limit)))
            partial = partial or any(row.get("partial") or row.get("stale") for row in rows)
            for row in rows:
                item = _surprise_item(row)
                item["series"] = name
                items.append(item)
        except Exception as primary_exc:
            errors[name] = str(primary_exc)
            partial = True
            if not fallback:
                continue
            env = series.macro_series(name, limit=limit)
            if env.get("ok"):
                for row in envelope_items(env):
                    item = _surprise_item(row)
                    item["series"] = name
                    item["data_quality"] = "actual_only_fallback"
                    item["primary_error"] = str(primary_exc)
                    items.append(item)
            else:
                errors[name] = "%s; fallback: %s" % (primary_exc, env.get("error"))
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
        partial=partial,
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


@operation("batch")
def rates_snapshot() -> dict:
    """LPR 最新 + SHIBOR 多期限快照（默认 ON/1W/1M/3M/1Y）。"""
    legs: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    lpr = lpr_history(limit=3)
    lpr_items = envelope_items(lpr)
    if lpr.get("ok") and lpr_items:
        legs["lpr"] = lpr_items[0]
    else:
        errors["lpr"] = lpr.get("error") or "lpr failed"

    shibor: Dict[str, Any] = {}
    for key in _SHIBOR_SNAPSHOT_KEYS:
        try:
            from scutio_data._providers.akshare.client import fetch

            tenor = _SHIBOR_TENORS[key]
            rows = fetch(
                "rate_interbank",
                market="上海银行同业拆借市场",
                symbol="Shibor人民币",
                indicator=tenor,
            )
            rows.sort(key=lambda row: str(row.get("报告日") or ""), reverse=True)
            if not rows:
                errors["shibor_%s" % key] = "empty"
                continue
            r0 = rows[0]
            shibor[key] = {
                "date": _date_str(r0.get("报告日")),
                "period": tenor,
                "rate": finite_number(r0.get("利率")),
                "change": finite_number(r0.get("涨跌")),
                "units": {"rate": "pct", "change": "bp"},
                "source": "akshare_rate_interbank",
            }
        except Exception as exc:
            errors["shibor_%s" % key] = str(exc)
    if shibor:
        legs["shibor"] = shibor

    if not legs:
        return result_err(
            "all rate legs failed: %s" % errors,
            source="rates_snapshot",
            errors=errors,
        )
    return result_ok(
        source="rates_snapshot",
        partial=bool(errors),
        errors=errors or None,
        **legs,
    )


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
    supplied = dict(preloaded or {})

    rates = supplied["rates_snapshot"] if "rates_snapshot" in supplied else rates_snapshot()
    if rates.get("ok"):
        payload["rates"] = {k: rates[k] for k in ("lpr", "shibor") if k in rates}
        if rates.get("errors"):
            errors["rates_partial"] = str(rates.get("errors"))
    else:
        errors["rates"] = rates.get("error") or "rates failed"

    bonds = (
        supplied["bond_yields_cn_us"]
        if "bond_yields_cn_us" in supplied
        else bond_yields_cn_us(limit=3)
    )
    bonds_items = envelope_items(bonds)
    if bonds.get("ok") and bonds_items:
        payload["bonds"] = bonds_items[0]
    else:
        errors["bonds"] = bonds.get("error") or "bonds failed"

    fx = supplied["fx_usdcny"] if "fx_usdcny" in supplied else quotes.fx_usdcny()
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

    idx = supplied["index_board"] if "index_board" in supplied else index_board()
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

    cmd = (
        supplied["commodities_spot"]
        if "commodities_spot" in supplied
        else quotes.commodities_spot()
    )
    if cmd.get("ok"):
        payload["commodities"] = {k: cmd[k] for k in ("gold", "wti") if cmd.get(k)}
        if cmd.get("errors"):
            errors["commodities_partial"] = str(cmd.get("errors"))
    else:
        errors["commodities"] = cmd.get("error") or "commodities failed"

    # 可选：中美关键序列各 1 点（失败不影响主快照）
    supplied_series = dict(supplied.get("series") or {})
    for sname in (
        "cpi_yoy",
        "pmi_mfg",
        "pmi_non_mfg",
        "forex_reserves",
        "rrr",
        "social_financing",
    ):
        ser = (
            supplied_series[sname]
            if sname in supplied_series
            else series.cn_macro_series(sname, limit=1)
        )
        sit = envelope_items(ser)
        if ser.get("ok") and sit and sit[0].get("value") is not None:
            payload.setdefault("latest_series", {})[sname] = sit[0]
            if ser.get("partial") or ser.get("stale"):
                errors["series_%s" % sname] = "partial or stale source coverage"
        else:
            errors["series_%s" % sname] = ser.get("error") or "empty"

    us_names = (
        "us_cpi_yoy",
        "us_unemployment",
        "us_nfp",
        "us_ism_pmi",
        "us_fed_funds_upper",
    )
    for sname in us_names:
        ser = (
            supplied_series[sname]
            if sname in supplied_series
            else series.us_macro_series(sname, limit=1)
        )
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
