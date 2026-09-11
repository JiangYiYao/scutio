"""Compose dated AKShare market tables into bounded per-stock histories.

This module owns windows, identities and units, not upstream HTTP protocols.
A failed/missing partition never implies a zero balance or no trading activity."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import partial

from scutio_data._providers.akshare import snapshots as akshare_snapshots
from scutio_data._runtime.environment import CN_TZ
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import require_a_share
from scutio_data._runtime.timeouts import operation
from scutio_data._runtime.timeouts import remaining as time_left
from scutio_data.batch import fetch_many


def _day(value):
    raw = str(value or "")[:10]
    if len(raw) == 8 and raw.isdigit():
        raw = "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:])
    return date.fromisoformat(raw)


def _code(value):
    raw = str(value or "").strip()
    if not raw.isdigit() or len(raw) > 6:
        raise ValueError("AKShare security code missing or invalid")
    return raw.zfill(6)


class HistoryQuery:
    def __init__(self, page_size, end_date, lookback_days):
        self.limit = max(1, int(page_size))
        self.end = _day(end_date) if end_date else datetime.now(CN_TZ).date() - timedelta(days=1)
        days = int(lookback_days)
        if not 1 <= days <= 3650:
            raise ValueError("lookback_days must be between 1 and 3650")
        self.start = self.end - timedelta(days=days - 1)
        self.snapshots = []
        self.errors = {}
        self.queried = []

    def fetch(self, function, **params):
        remaining = time_left("batch_partition")
        rows, meta = akshare_snapshots.fetch_snapshot(
            function, _timeout_seconds=remaining, **params
        )
        self.snapshots.append(meta)
        return rows

    def result(self, items, source, *, missing_fields=(), **meta):
        items.sort(key=lambda row: row["date"], reverse=True)
        stamps = [
            s["snapshot_retrieved_at"]
            for s in self.snapshots
            if s.get("snapshot_retrieved_at") is not None
        ]
        coverage = {
            "requested_count": self.limit,
            "available_count": len(items),
            "requested_count_met": len(items) >= self.limit,
            "lookback_window": {"start": str(self.start), "end": str(self.end)},
            "queried_partitions": self.queried,
            "failed_partitions": self.errors,
            "missing_fields": list(missing_fields),
            "all_history": False,
        }
        metadata = dict(
            adapter="akshare",
            coverage=coverage,
            partial=bool(self.errors or missing_fields or len(items) < self.limit),
            snapshot_time_range={"oldest": min(stamps), "newest": max(stamps)} if stamps else None,
            cached_partitions=sum(bool(s.get("snapshot_cached")) for s in self.snapshots),
            **meta,
        )
        if self.errors and not items:
            return result_list_err("; ".join(self.errors.values()), source=source, **metadata)
        return result_list(items[: self.limit], source=source, **metadata)


@operation("batch")
def margin_history(code, page_size=30, *, end_date=None, lookback_days=90):
    from scutio_data._runtime.parsing import finite_number

    query = HistoryQuery(page_size, end_date, lookback_days)
    _, exchange, pure = require_a_share(code, "margin_history")
    function = {
        "sh": "stock_margin_detail_sse",
        "sz": "stock_margin_detail_szse",
        "bj": "stock_margin_detail_bse",
    }[exchange]
    items = []
    try:
        calendar = sorted(
            {_day(row["trade_date"]) for row in query.fetch("tool_trade_date_hist_sina")}
        )
        if not calendar or calendar[-1] < query.end:
            raise ValueError("AKShare trading calendar does not cover requested end date")
        days = [day for day in reversed(calendar) if query.start <= day <= query.end]
    except Exception as exc:
        query.errors["calendar"] = str(exc)
        return query.result(items, "margin_trading")
    code_field = "标的证券代码" if exchange == "sh" else "证券代码"
    fields = {
        "融资余额": "rzye",
        "融资买入额": "rzmre",
        "融资偿还额": "rzche",
        "融券余额": "rqye",
        "融券卖出量": "rqmcl",
        "融券偿还量": "rqchl",
        "融资融券余额": "rzrqye",
        "融券余量": "rqyl",
    }
    missing = ("rqye", "rzrqye") if exchange == "sh" else ("rzche", "rqchl")
    for day in days:
        try:
            rows = query.fetch(function, date=day.strftime("%Y%m%d"))
            if exchange == "sh" and len(rows) >= 5000:
                raise ValueError("SSE margin snapshot may be truncated at 5000 rows")
            if not rows:
                raise ValueError(
                    "empty market margin table on trading day; not evidence of zero balances"
                )
            matched = []
            for row in rows:
                identity = _code(row.get(code_field))
                if exchange == "sh" and _day(row.get("信用交易日期")) != day:
                    raise ValueError("SSE margin response date mismatch")
                if identity == pure:
                    matched.append(row)
            if len(matched) > 1:
                raise ValueError("duplicate margin security/date")
            query.queried.append(str(day))
            if matched:
                items.append(
                    {
                        "date": str(day),
                        **{
                            dest: finite_number(matched[0].get(src)) for src, dest in fields.items()
                        },
                    }
                )
            if len(items) >= query.limit:
                break
        except Exception as exc:
            query.errors[str(day)] = str(exc)
            break  # Avoid repeating a failing upstream across older dates.
    return query.result(
        items,
        "margin_trading",
        missing_fields=missing,
        exchange=exchange,
        date_basis="provider" if exchange == "sh" else "requested_date",
        units={
            "rzye": "元",
            "rzmre": "元",
            "rzche": "元",
            "rqye": "元",
            "rzrqye": "元",
            "rqmcl": "股",
            "rqchl": "股",
            "rqyl": "股",
        },
    )


@operation("batch")
def block_history(code, page_size=20, *, end_date=None, lookback_days=365):
    from scutio_data._runtime.parsing import finite_number

    query = HistoryQuery(page_size, end_date, lookback_days)
    _, _, pure = require_a_share(code, "block_history")
    items = []

    def partition(start, end):
        rows = query.fetch(
            "stock_dzjy_mrmx",
            symbol="A股",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if len(rows) >= 5000 and start < end:
            middle = start + (end - start) // 2
            partition(middle + timedelta(days=1), end)
            if len(items) < query.limit:
                partition(start, middle)
            return
        if len(rows) >= 5000:
            raise ValueError("one-day block trade table reached 5000-row limit")
        converted = []
        for row in rows:
            day = _day(row.get("交易日期"))
            if not start <= day <= end:
                raise ValueError("block trade response date outside requested partition")
            if _code(row.get("证券代码")) != pure:
                continue
            close, price = finite_number(row.get("收盘价")), finite_number(row.get("成交价"))
            converted.append(
                {
                    "date": str(day),
                    "price": price,
                    "close": close,
                    "premium_pct": round((price / close - 1) * 100, 2)
                    if price is not None and close
                    else None,
                    "vol": finite_number(row.get("成交量")),
                    "amount": finite_number(row.get("成交额")),
                    "buyer": row.get("买方营业部") or "",
                    "seller": row.get("卖方营业部") or "",
                }
            )
        # Equal-looking deals can be distinct trades; do not deduplicate them.
        items.extend(converted)
        query.queried.append({"start": str(start), "end": str(end)})

    end = query.end
    while end >= query.start and len(items) < query.limit:
        start = max(query.start, end - timedelta(days=29))
        try:
            partition(start, end)
        except Exception as exc:
            query.errors["%s..%s" % (start, end)] = str(exc)
            break
        end = start - timedelta(days=1)
    return query.result(
        items, "block_trade", units={"vol": "股", "amount": "元", "price": "元", "premium_pct": "%"}
    )


@operation("batch")
def pledge_history(code, page_size=20, *, end_date=None, lookback_days=730):
    from scutio_data._runtime.parsing import finite_number

    query = HistoryQuery(page_size, end_date, lookback_days)
    _, _, pure = require_a_share(code, "pledge_history")
    items = []
    try:
        dates = sorted(
            {_day(row["交易日期"]) for row in query.fetch("stock_gpzy_profile_em")}, reverse=True
        )
    except Exception as exc:
        query.errors["published_dates"] = str(exc)
        return query.result(items, "eastmoney_csdc_pledge")
    fields = {
        "质押比例": "pledge_ratio_pct",
        "质押股数": "pledged_shares_wan",
        "质押市值": "pledged_value_wan",
        "质押笔数": "pledge_count",
        "无限售股质押数": "unrestricted_pledged_shares_wan",
        "限售股质押数": "restricted_pledged_shares_wan",
    }
    for day in dates:
        if not query.start <= day <= query.end:
            continue
        try:
            rows = query.fetch("stock_gpzy_pledge_ratio_em", date=day.strftime("%Y%m%d"))
            if not rows:
                raise ValueError("published pledge date has no market snapshot")
            matched = []
            for row in rows:
                if _day(row.get("交易日期")) != day:
                    raise ValueError("pledge snapshot date mismatch")
                if _code(row.get("股票代码")) == pure:
                    matched.append(row)
            if len(matched) > 1:
                raise ValueError("duplicate pledge security/date")
            query.queried.append(str(day))
            if matched:
                row = matched[0]
                items.append(
                    {
                        "code": pure,
                        "name": row.get("股票简称") or "",
                        "date": str(day),
                        "industry": row.get("所属行业") or "",
                        "source": "eastmoney_csdc_pledge",
                        **{dest: finite_number(row.get(src)) for src, dest in fields.items()},
                    }
                )
            if len(items) >= query.limit:
                break
        except Exception as exc:
            query.errors[str(day)] = str(exc)
            break
    return query.result(
        items,
        "eastmoney_csdc_pledge",
        code=pure,
        market="a",
        latest_published_date=str(dates[0]) if dates else None,
        units={"*_shares_wan": "万股", "pledged_value_wan": "万元", "pledge_ratio_pct": "%"},
    )


def _shareholder_snapshot(label):
    from scutio_data._providers.akshare.errors import AKShareError

    try:
        rows, snapshot = akshare_snapshots.fetch_snapshot(
            "stock_ggcg_em", symbol="股东" + label, _timeout_seconds=time_left("batch_partition")
        )
    except AKShareError as exc:
        return result_list_err(str(exc), source="eastmoney_holder_change", error_code=exc.code)
    return result_list(rows, snapshot=snapshot)


@operation("batch")
def shareholder_history(code, direction="all", page_size=50):
    from scutio_data._runtime.parsing import finite_number

    _, exchange, pure = require_a_share(code, "shareholder_history")
    directions = {
        "all": ("增持", "减持"),
        "increase": ("增持",),
        "decrease": ("减持",),
        "增持": ("增持",),
        "减持": ("减持",),
    }
    selected = directions.get(str(direction or "all").strip().lower())
    if selected is None:
        raise ValueError("direction must be all/increase/decrease")
    limit = max(1, int(page_size))
    items, errors, snapshots, completed = [], {}, [], []
    fields = {
        "持股变动信息-变动数量": "shares_changed_wan",
        "持股变动信息-占总股本比例": "total_share_pct",
        "持股变动信息-占流通股比例": "float_share_pct",
        "变动后持股情况-持股总数": "shares_after_wan",
        "变动后持股情况-占总股本比例": "after_total_share_pct",
        "变动后持股情况-持流通股数": "float_shares_after_wan",
        "变动后持股情况-占流通股比例": "after_float_share_pct",
    }
    results = fetch_many({label: partial(_shareholder_snapshot, label) for label in selected})[
        "results"
    ]
    for label in selected:
        try:
            env = results[label]["result"]
            if not env.get("ok"):
                raise ValueError(env.get("error") or "shareholder fetch failed")
            rows, snapshot = env["items"], env["snapshot"]
            leg_items = []
            for row in rows:
                identity = _code(row.get("代码"))
                if row.get("持股变动信息-增减") != label:
                    raise ValueError("shareholder direction mismatch")
                if identity != pure:
                    continue
                changed = finite_number(row.get("持股变动信息-变动数量"))

                def event_date(key):
                    value = row.get(key)
                    return str(_day(value)) if value else ""

                leg_items.append(
                    {
                        "code": pure,
                        "name": row.get("名称") or "",
                        "holder": row.get("股东名称") or "",
                        "direction": label,
                        **{dest: finite_number(row.get(src)) for src, dest in fields.items()},
                        "signed_shares_changed_wan": None
                        if changed is None
                        else abs(changed) * (1 if label == "增持" else -1),
                        "trade_average_price": None,
                        "start_date": event_date("变动开始日"),
                        "end_date": event_date("变动截止日"),
                        "announcement_date": event_date("公告日"),
                        "source": "eastmoney_holder_change",
                    }
                )
            # Do not retain a half-parsed directional table after a schema error.
            items.extend(leg_items)
            snapshots.append(dict(direction=label, **snapshot))
            completed.append(label)
        except Exception as exc:
            errors[label] = str(exc)
    meta = dict(
        adapter="akshare",
        market="a",
        code=pure,
        partial=True,
        errors=errors,
        coverage={
            "requested_directions": list(selected),
            "completed_directions": completed,
            "missing_directions": list(errors),
            "trade_average_price": False,
        },
        snapshots=snapshots,
        derived_fields=["signed_shares_changed_wan"],
        warning="Average trade price is not exposed; signed quantity derives from direction",
        units={"*_wan": "万股", "*_pct": "%", "trade_average_price": "元"},
    )
    if not completed:
        return result_list_err("; ".join(errors.values()), source="shareholder_changes", **meta)
    items.sort(key=lambda row: (row["end_date"], row["announcement_date"]), reverse=True)
    return result_list(items[:limit], source="eastmoney_holder_change", **meta)
