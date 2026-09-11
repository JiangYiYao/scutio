"""交易日历、停复牌与公司事件日历。

边界：A 股优先完整覆盖；港美仅在公开源有稳定语义时提供。所有门面返回
``result_*`` 信封，backup 只在主源请求失败时接管，不把不同口径拼成同一张表。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List

import scutio_data._providers.akshare.economic_calendar as calendar_source
import scutio_data._providers.eastmoney_events as performance_source
from scutio_data._providers.akshare.economic_calendar import (
    map_suspension_rows as map_suspension_rows,
)
from scutio_data._providers.eastmoney_events import _PERFORMANCE_REPORTS as _PERFORMANCE_REPORTS
from scutio_data._providers.eastmoney_events import (
    map_performance_update_rows as map_performance_update_rows,
)
from scutio_data._runtime.dates import date_arg as date_arg
from scutio_data._runtime.dates import iso_date as iso_date
from scutio_data._runtime.environment import CN_TZ
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import market_of, require_a_share, require_company
from scutio_data._runtime.timeouts import operation

__all__ = [
    "trade_calendar",
    "suspensions",
    "earnings_calendar",
    "performance_updates",
    "company_events",
    "map_suspension_rows",
    "map_performance_update_rows",
]


_CALENDAR_NAMES = {"a": "XSHG", "hk": "XHKG", "us": "XNYS"}


_CALENDAR_BARS = {"a": "sh000001", "hk": "hk02800", "us": "usSPY"}


def _market_key(market: str) -> str:
    key = str(market or "a").strip().lower()
    aliases = {
        "cn": "a",
        "china": "a",
        "xshg": "a",
        "h": "hk",
        "hongkong": "hk",
        "xhkg": "hk",
        "usa": "us",
        "nyse": "us",
        "nasdaq": "us",
        "xnys": "us",
    }
    key = aliases.get(key, key)
    if key not in _CALENDAR_NAMES:
        raise ValueError("market must be a/hk/us")
    return key


def _calendar_local(market: str, start: date, end: date) -> List[dict]:
    """exchange-calendars 本地规则表；无网络、支持未来已发布假期。"""
    try:
        import exchange_calendars as xcals
    except ImportError as exc:
        raise RuntimeError("exchange-calendars required for trade_calendar") from exc
    cal = xcals.get_calendar(_CALENDAR_NAMES[market])
    sessions = cal.sessions_in_range(start.isoformat(), end.isoformat())
    out: List[dict] = []
    for session in sessions:
        opened = cal.session_open(session)
        closed = cal.session_close(session)
        out.append(
            {
                "date": str(session.date()),
                "market": market,
                "exchange_calendar": _CALENDAR_NAMES[market],
                "open": opened.isoformat(),
                "close": closed.isoformat(),
                "is_open": True,
            }
        )
    return out


def _calendar_from_bars(market: str, start: date, end: date) -> List[dict]:
    """历史 K 线推导 backup；只证明实际发生过的交易日，不推断未来。"""
    if end > datetime.now(CN_TZ).date():
        raise RuntimeError("bars backup cannot infer future sessions")
    from scutio_data.market import security_bars

    count = min(5000, max(80, (end - start).days * 2 + 20))
    env = security_bars(_CALENDAR_BARS[market], frequency="D", count=count)
    if not env.get("ok"):
        raise RuntimeError(env.get("error") or "calendar bars failed")
    dates = []
    for row in env.get("bars") or []:
        day = iso_date(row.get("date") or row.get("datetime") or row.get("time"))
        if day and start.isoformat() <= day <= end.isoformat():
            dates.append(day)
    if not dates:
        raise RuntimeError("calendar bars empty in requested range")
    return [
        {
            "date": day,
            "market": market,
            "exchange_calendar": None,
            "open": None,
            "close": None,
            "is_open": True,
            "data_quality": "historical_bars_backup",
        }
        for day in sorted(set(dates))
    ]


@operation("history")
def trade_calendar(market="a", start_date=None, end_date=None, fallback=True):
    """交易日历（A/港/美）。

    主路径为 ``exchange-calendars`` 的交易所规则表；依赖缺失/规则异常时，过去区间
    可由代表性标的真实日 K 推导 backup。K 线 backup 不会冒充未来日历。
    """
    try:
        key = _market_key(market)
        today = datetime.now(CN_TZ).date()
        start = date_arg(start_date, default=today - timedelta(days=30))
        end = date_arg(end_date, default=today + timedelta(days=30))
        if end < start:
            raise ValueError("end_date must be >= start_date")
        try:
            items = _calendar_local(key, start, end)
            return result_list(
                items,
                source="exchange_calendars:%s" % _CALENDAR_NAMES[key],
                market=key,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                backup_used=False,
            )
        except Exception as primary_exc:
            if not fallback:
                raise
            try:
                items = _calendar_from_bars(key, start, end)
                return result_list(
                    items,
                    source="market_bars_calendar_backup",
                    market=key,
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                    backup_used=True,
                    partial=True,
                    primary_error=str(primary_exc),
                    note="仅历史实际交易日；不能用来推断未来开市日",
                )
            except Exception as backup_exc:
                raise RuntimeError(
                    "primary: %s; backup: %s" % (primary_exc, backup_exc)
                ) from backup_exc
    except Exception as exc:
        return result_list_err(str(exc), source="trade_calendar")


@operation("batch")
def suspensions(day=None, code=None, market="a", fallback=True):
    """指定日期停复牌。A 股东财主源；A/港可用百度日历 backup。"""
    try:
        key = market_of(code) if code else _market_key(market)
        query_day = date_arg(day, default=datetime.now(CN_TZ).date()).isoformat()
        pure = require_company(code, "suspensions")[2] if code else ""
        primary_error = None
        if key == "a":
            try:
                from scutio_data._providers.akshare.snapshots import fetch_snapshot

                rows, snapshot = fetch_snapshot("stock_tfp_em", date=query_day.replace("-", ""))
                items = []
                for row in rows:
                    identity = str(row.get("代码") or "")
                    if not identity.isdigit() or len(identity) > 6:
                        raise ValueError("AKShare suspension identity missing")
                    start, end = iso_date(row.get("停牌时间")), iso_date(row.get("停牌截止时间"))
                    expected = iso_date(row.get("预计复牌时间"))
                    status = (
                        "scheduled"
                        if start and start > query_day
                        else "unknown"
                        if not start
                        or (end and end < query_day)
                        or (expected and expected <= query_day)
                        else "suspended"
                    )
                    items.append(
                        {
                            "code": identity.zfill(6),
                            "name": row.get("名称") or "",
                            "suspend_date": start,
                            "suspend_end_date": end,
                            "resume_date": expected,
                            "resume_date_basis": "expected",
                            "reason": row.get("停牌原因") or "",
                            "duration": row.get("停牌期限"),
                            "exchange": row.get("所属市场") or "",
                            "status": status,
                            "source": "eastmoney_suspend",
                        }
                    )
                if pure:
                    items = [x for x in items if x["code"] == pure]
                status = (
                    "suspended"
                    if any(x["status"] == "suspended" for x in items)
                    else "scheduled"
                    if items and all(x["status"] == "scheduled" for x in items)
                    else "unknown"
                )
                return result_list(
                    items,
                    source="eastmoney_suspend",
                    adapter="akshare",
                    market=key,
                    date=query_day,
                    backup_used=False,
                    status=status if pure else None,
                    partial=True,
                    coverage="provider_suspension_snapshot",
                    date_basis="requested_date",
                    note="Source may include future plans; expected resumption does not prove actual trading status",
                    **snapshot,
                )
            except Exception as exc:
                primary_error = str(exc)
                if not fallback:
                    raise
        elif key not in ("hk",):
            return result_list_err(
                "unsupported_market: suspensions supports a/hk; got %s" % key,
                source="suspensions",
                market=key,
            )
        try:
            items = calendar_source.calendar_rows(query_day, "notify_suspend")
            items = [
                item
                for item in items
                if str(item.get("exchange") or "").upper()
                in (("HK",) if key == "hk" else ("SH", "SZ", "BJ"))
            ]
            if pure:
                items = [
                    x
                    for x in items
                    if str(x.get("code") or "").zfill(5 if key == "hk" else 6) == pure
                ]
            return result_list(
                items,
                source="baidu_finance_calendar",
                market=key,
                date=query_day,
                backup_used=(key == "a"),
                primary_error=primary_error,
                status=("unknown" if pure else None),
                partial=True,
                coverage="suspension_events_on_date",
                note="百度为当日停复牌事件日历；无记录不能证明证券处于正常交易状态",
            )
        except Exception as backup_exc:
            message = "baidu: %s" % backup_exc
            if primary_error:
                message = "eastmoney: %s; %s" % (primary_error, message)
            return result_list_err(message, source="suspensions", market=key)
    except Exception as exc:
        return result_list_err(str(exc), source="suspensions")


def _ak_earnings(report_date, segment):
    from scutio_data._providers.akshare.snapshots import fetch_snapshot

    rows, snapshot = fetch_snapshot(
        "stock_yysj_em", symbol=segment, date=report_date.replace("-", "")
    )
    items = []
    for row in rows:
        code = str(row.get("股票代码") or "")
        if not code.isdigit() or len(code) > 6:
            raise ValueError("AKShare disclosure calendar identity missing")
        first = iso_date(row.get("首次预约时间"))
        changes = [
            iso_date(row.get(label)) for label in ("一次变更日期", "二次变更日期", "三次变更日期")
        ]
        items.append(
            {
                "code": code.zfill(6),
                "name": row.get("股票简称") or "",
                "report_date": report_date,
                "first_appointment": first,
                "change_dates": [day for day in changes if day],
                "scheduled_date": next((day for day in reversed(changes) if day), first),
                "actual_date": iso_date(row.get("实际披露时间")),
                "source": "eastmoney_disclosure_calendar",
            }
        )
    return items, snapshot


@operation("batch")
def earnings_calendar(report_date, code=None, market="a", fallback=True):
    """AKShare 预约披露日历；fallback 参数保留兼容，不调用旧直连备用。

    report_date 为报告期末。预约日期与实际披露日期分开返回。
    """
    try:
        key = market_of(code) if code else _market_key(market)
        if key != "a":
            return result_list_err(
                "unsupported_market: earnings_calendar supports A shares",
                source="earnings_calendar",
                market=key,
            )
        period = date_arg(report_date, default=datetime.now(CN_TZ).date()).isoformat()
        if period[5:] not in ("03-31", "06-30", "09-30", "12-31"):
            raise ValueError("report_date must be a quarter/year end")
        from scutio_data._runtime.symbols import split_code

        pure = require_a_share(code, "earnings_calendar")[2] if code else ""
        segments = (
            ["京市A股" if split_code(code)[0] == "bj" else "沪深A股"]
            if code
            else ["沪深A股", "京市A股"]
        )
        items, errors, snapshots = [], {}, []
        for segment in segments:
            try:
                rows, snapshot = _ak_earnings(period, segment)
                snapshots.append(dict(segment=segment, **snapshot))
                items.extend(row for row in rows if not pure or row["code"] == pure)
            except Exception as exc:
                errors[segment] = str(exc)
        if len(errors) == len(segments):
            return result_list_err(
                "; ".join(errors.values()), source="earnings_calendar", errors=errors
            )
        items.sort(key=lambda row: (row["scheduled_date"] or "9999", row["code"]))
        return result_list(
            items,
            source="eastmoney_disclosure_calendar",
            adapter="akshare",
            market="a",
            report_date=period,
            report_date_basis="requested_period",
            backup_used=False,
            partial=bool(errors),
            errors=errors,
            snapshots=snapshots,
            coverage={"requested_segments": segments, "missing_segments": list(errors)},
        )
    except Exception as exc:
        return result_list_err(str(exc), source="earnings_calendar")


@operation("batch")
def performance_updates(report_date, kind="all", code=None):
    """A 股业绩预告/业绩快报。

    ``kind`` 为 ``forecast`` / ``express`` / ``all``。结构化表当前为东财单源；
    巨潮公告只能作原文证据，不能直接替代这些数值字段。
    """
    try:
        key = str(kind or "all").strip().lower()
        if key not in ("forecast", "express", "all"):
            raise ValueError("kind must be forecast/express/all")
        if code and market_of(code) != "a":
            return result_list_err(
                "unsupported_market: performance_updates currently supports A shares",
                source="performance_updates",
            )
        period = date_arg(report_date, default=datetime.now(CN_TZ).date()).isoformat()
        pure = require_a_share(code, "performance_updates")[2] if code else ""
        kinds = ("forecast", "express") if key == "all" else (key,)
        items = []
        errors = {}
        for item_kind in kinds:
            try:
                items.extend(performance_source._performance_rows(period, item_kind, pure))
            except Exception as exc:
                errors[item_kind] = str(exc)
        items.sort(
            key=lambda row: (row.get("notice_date") or "", row.get("code") or ""), reverse=True
        )
        if not items and errors:
            return result_list_err(
                "; ".join("%s: %s" % pair for pair in errors.items()),
                source="performance_updates",
                errors=errors,
            )
        return result_list(
            items,
            source="performance_updates",
            market="a",
            report_date=period,
            kind=key,
            partial=bool(errors),
            errors=errors,
            note="结构化东财单源；巨潮公告仅作原文证据，不作为同构数值 backup",
        )
    except Exception as exc:
        return result_list_err(str(exc), source="performance_updates")


@operation("batch")
def company_events(day=None, code=None, page_size=5000):
    """A 股公司动态日历（业绩、分红、股东大会等东财事件摘要）。"""
    try:
        query_day = date_arg(day, default=datetime.now(CN_TZ).date()).isoformat()
        pure = require_a_share(code, "company_events")[2] if code else ""
        if code and market_of(code) != "a":
            return result_list_err(
                "unsupported_market: company_events currently supports A shares",
                source="company_events",
            )
        from scutio_data._providers.akshare.client import fetch

        source_rows = fetch("stock_gsrl_gsdt_em", date=query_day.replace("-", ""))
        mapping = {
            "代码": "SECURITY_CODE",
            "简称": "SECURITY_NAME_ABBR",
            "事件类型": "EVENT_TYPE",
            "具体事项": "EVENT_CONTENT",
            "交易日": "TRADE_DATE",
        }
        rows = [{target: row.get(col) for col, target in mapping.items()} for row in source_rows]
        if any(iso_date(row.get("TRADE_DATE")) != query_day for row in rows):
            raise ValueError("company calendar date mismatch")
        items = [
            {
                "code": str(row.get("SECURITY_CODE") or "").zfill(6),
                "name": row.get("SECURITY_NAME_ABBR") or "",
                "event_type": row.get("EVENT_TYPE") or "",
                "content": row.get("EVENT_CONTENT") or "",
                "date": iso_date(row.get("TRADE_DATE")) or query_day,
                "source": "eastmoney_company_calendar",
            }
            for row in rows
        ]
        if pure:
            items = [x for x in items if x.get("code") == pure]
        return result_list(
            items[: max(1, int(page_size))],
            partial=len(source_rows) >= 5000 or len(items) > max(1, int(page_size)),
            coverage={"source_row_cap": 5000, "total_seen": len(source_rows)},
            adapter="akshare",
            source="eastmoney_company_calendar",
            market="a",
            date=query_day,
            data_quality="discovery_only",
            authority="summary",
            note="仅用于发现事件线索；业绩、披露、分红、回购等事实以对应结构化门面或原公告为准",
        )
    except Exception as exc:
        return result_list_err(str(exc), source="company_events")
