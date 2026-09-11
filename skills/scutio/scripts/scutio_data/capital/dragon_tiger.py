"""AKShare Dragon Tiger records and seats; never merge different listing reasons."""

from datetime import datetime, timedelta

from scutio_data._providers.akshare import snapshots as akshare_snapshots
from scutio_data._runtime.environment import CN_TZ
from scutio_data._runtime.parsing import finite_number
from scutio_data._runtime.results import result_err, result_ok
from scutio_data._runtime.symbols import require_a_share
from scutio_data._runtime.timeouts import operation
from scutio_data._runtime.timeouts import remaining as time_left


def _day(value):
    return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date().isoformat()


def _number(value, divisor=1, precision=2):
    number = finite_number(value)
    return None if number is None else round(number / divisor, precision)


def _records(start, end):
    rows, snapshot = akshare_snapshots.fetch_snapshot(
        "stock_lhb_detail_em",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        _timeout_seconds=time_left("batch_partition"),
    )
    for row in rows:
        code = str(row.get("代码") or "")
        if not code.isdigit() or len(code) > 6:
            raise ValueError("Dragon Tiger security identity missing")
        if not start <= _day(row.get("上榜日")) <= end:
            raise ValueError("Dragon Tiger date outside requested window")
    return rows, snapshot


@operation("batch")
def daily(trade_date=None, min_net_buy=None):
    day = trade_date or datetime.now(CN_TZ).strftime("%Y-%m-%d")
    try:
        day = _day(day)
        rows, snapshot = _records(day, day)
        stocks = []
        unknown = 0
        for row in rows:
            original = finite_number(row.get("龙虎榜净买额"))
            net = _number(original, 10000, 1)
            if min_net_buy is not None and (
                original is None or original / 10000 < float(min_net_buy)
            ):
                unknown += net is None
                continue
            stocks.append(
                {
                    "code": str(row["代码"]).zfill(6),
                    "name": row.get("名称") or "",
                    "reason": row.get("上榜原因") or "",
                    "close": finite_number(row.get("收盘价")),
                    "change_pct": _number(row.get("涨跌幅")),
                    "net_buy_wan": net,
                    "buy_wan": _number(row.get("龙虎榜买入额"), 10000, 1),
                    "sell_wan": _number(row.get("龙虎榜卖出额"), 10000, 1),
                    "turnover_pct": _number(row.get("换手率")),
                }
            )
        stocks.sort(
            key=lambda row: (row["net_buy_wan"] is not None, row["net_buy_wan"] or 0), reverse=True
        )
        return result_ok(
            source="daily_dragon_tiger",
            adapter="akshare",
            date=day,
            total_records=len(stocks),
            stocks=stocks,
            partial=bool(unknown),
            excluded_unknown_net_buy=unknown,
            units={"*_wan": "万元"},
            **snapshot,
        )
    except Exception as exc:
        return result_err(
            str(exc), source="daily_dragon_tiger", date=day, total_records=0, stocks=[]
        )


@operation("batch")
def board(code, trade_date, look_back=30):
    records, seats, groups = [], {"buy": [], "sell": []}, {}
    institution = {"buy_amt": None, "sell_amt": None, "net_amt": None}
    try:
        _, _, pure = require_a_share(code, "dragon_tiger_board")
        end = _day(trade_date)
        days = int(look_back)
        if not 0 <= days <= 365:
            raise ValueError("look_back must be between 0 and 365 days")
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        rows, snapshot = _records(start, end)
        selected = [row for row in rows if str(row["代码"]).zfill(6) == pure]
        selected.sort(key=lambda row: _day(row["上榜日"]), reverse=True)
        records = [
            {
                "date": _day(row["上榜日"]),
                "reason": row.get("上榜原因") or "",
                "net_buy": _number(row.get("龙虎榜净买额"), 10000, 1),
                "turnover": _number(row.get("换手率")),
            }
            for row in selected
        ]
        errors, institution_records = {}, []
        snapshots = [dict(part="records", **snapshot)]
        if records:
            day = records[0]["date"]
            for side, flag in (("buy", "买入"), ("sell", "卖出")):
                try:
                    remaining = time_left("batch_partition")
                    raw, part_snapshot = akshare_snapshots.fetch_snapshot(
                        "stock_lhb_stock_detail_em",
                        symbol=pure,
                        date=day.replace("-", ""),
                        flag=flag,
                        _timeout_seconds=remaining,
                    )
                    snapshots.append(dict(part=side, **part_snapshot))
                    if not raw:
                        raise ValueError("seat table empty for listed security/date")
                    if len(raw) >= 500:
                        raise ValueError("seat table may be truncated at 500 rows")
                    side_groups = {}
                    for row in raw:
                        reason = str(row.get("类型") or "")
                        if not reason:
                            raise ValueError("seat listing reason missing")
                        item = {
                            "name": row.get("交易营业部名称") or "",
                            "reason": reason,
                            "buy_amt": _number(row.get("买入金额"), 10000, 1),
                            "sell_amt": _number(row.get("卖出金额"), 10000, 1),
                            "net": _number(row.get("净额"), 10000, 1),
                        }
                        side_groups.setdefault(reason, []).append(item)
                    for reason, entries in side_groups.items():
                        field = "buy_amt" if side == "buy" else "sell_amt"
                        entries.sort(
                            key=lambda item: (item[field] is not None, item[field] or 0),
                            reverse=True,
                        )
                        groups.setdefault(reason, {"buy": [], "sell": []})[side] = entries[:5]
                except Exception as exc:
                    errors[side] = str(exc)
            # A flat view is unambiguous only for one reason; all reasons remain available.
            if len(groups) == 1:
                seats = next(iter(groups.values()))
            try:
                remaining = time_left("batch_partition")
                raw, part_snapshot = akshare_snapshots.fetch_snapshot(
                    "stock_lhb_jgmmtj_em",
                    start_date=day.replace("-", ""),
                    end_date=day.replace("-", ""),
                    _timeout_seconds=remaining,
                )
                snapshots.append(dict(part="institution", **part_snapshot))
                for row in raw:
                    if _day(row.get("上榜日期")) != day:
                        raise ValueError("institution table date mismatch")
                    if str(row.get("代码") or "").zfill(6) == pure:
                        institution_records.append(
                            {
                                "date": day,
                                "reason": row.get("上榜原因") or "",
                                "buy_amt": _number(row.get("机构买入总额"), 10000, 1),
                                "sell_amt": _number(row.get("机构卖出总额"), 10000, 1),
                                "net_amt": _number(row.get("机构买入净额"), 10000, 1),
                            }
                        )
                if len(institution_records) == 1:
                    institution = {key: institution_records[0][key] for key in institution}
            except Exception as exc:
                institution_records = []
                errors["institution"] = str(exc)
        expected_reasons = (
            {row["reason"].strip() for row in records if row["date"] == records[0]["date"]}
            if records
            else set()
        )
        missing_reasons = sorted(expected_reasons - set(groups))
        partial = bool(
            errors
            or missing_reasons
            or len(groups) > 1
            or (records and len(institution_records) != 1)
        )
        return result_ok(
            source="dragon_tiger_board",
            adapter="akshare",
            records=records,
            seats=seats,
            seat_groups=groups,
            institution=institution,
            institution_records=institution_records,
            partial=partial,
            errors=errors,
            snapshots=snapshots,
            query_window={"start": start, "end": end},
            coverage={
                "seat_identity_basis": "requested_code_and_date",
                "seat_groups_preserved": True,
                "flat_seats_available": len(groups) == 1,
                "missing_seat_reasons": missing_reasons,
                "institution_available": len(institution_records) == 1,
            },
            institution_reason=institution_records[0]["reason"]
            if len(institution_records) == 1
            else None,
            units={"seat_and_institution_amounts": "万元", "net_buy": "万元"},
            **snapshot,
        )
    except Exception as exc:
        return result_err(
            str(exc),
            source="dragon_tiger_board",
            records=records,
            seats=seats,
            institution=institution,
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )
