"""AKShare 百度逐日日历组合；使用共同的区间请求预算。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, List

from scutio_data._runtime.dates import date_arg as date_arg
from scutio_data._runtime.dates import iso_date as iso_date
from scutio_data._runtime.environment import CN_TZ
from scutio_data._runtime.timeouts import operation, remaining


class CalendarError(RuntimeError):
    """A failed day retains only fully validated earlier partitions."""

    def __init__(self, cause, *, items, completed_days, failed_date):
        super().__init__(str(cause))
        self.items = items
        self.completed_days = completed_days
        self.failed_date = failed_date


@operation("batch")
def calendar_rows(day, cate, *, end_day=None):
    """Compose at most 14 daily AKShare tables within one shared operation budget."""
    from scutio_data._providers.akshare.client import fetch

    start = date_arg(day, default=datetime.now(CN_TZ).date())
    end = date_arg(end_day or day, default=start)
    if not 0 <= (end - start).days < 14:
        raise ValueError("calendar window must be 1-14 days")
    function = {
        "economic_data": "news_economic_baidu",
        "notify_suspend": "news_trade_notify_suspend_baidu",
    }[cate]
    items = []
    for offset in range((end - start).days + 1):
        current = start + timedelta(days=offset)
        day_start = len(items)
        try:
            rows = fetch(
                function,
                date=current.strftime("%Y%m%d"),
                _timeout_seconds=remaining("batch_partition"),
            )
            if cate == "notify_suspend":
                for row in rows:
                    mapped = map_suspension_rows([row], source="akshare_baidu_calendar")[0]
                    mapped.update(
                        exchange=row.get("交易所代码") or "",
                        market=row.get("市场类型"),
                        status="unknown",
                        resume_date_basis="calendar_event",
                    )
                    items.append(mapped)
            else:
                for row in rows:
                    if iso_date(row.get("日期")) != current.isoformat():
                        raise ValueError("calendar date mismatch")
                    items.append(
                        {
                            "date": current.isoformat(),
                            "time": row.get("时间") or "",
                            "country": row.get("国家") or "",
                            "region": row.get("地区") or "",
                            "event": row.get("事件") or "",
                            "period": row.get("统计周期") or "",
                            "actual": row.get("公布"),
                            "forecast": row.get("预期"),
                            "previous": row.get("前值"),
                            "importance": row.get("重要性"),
                            "source": "akshare_baidu_calendar",
                        }
                    )
        except Exception as exc:
            raise CalendarError(
                exc, items=items[:day_start], completed_days=offset, failed_date=current.isoformat()
            ) from exc
    return items


def map_suspension_rows(rows: Iterable[dict], *, source: str) -> List[dict]:
    out = []
    for row in rows or []:
        code = str(row.get("SECURITY_CODE") or row.get("code") or row.get("股票代码") or "").strip()
        out.append(
            {
                "code": code,
                "name": row.get("SECURITY_NAME_ABBR")
                or row.get("name")
                or row.get("股票简称")
                or "",
                "suspend_date": iso_date(
                    row.get("SUSPEND_START_DATE") or row.get("start") or row.get("停牌时间")
                ),
                "resume_date": iso_date(
                    row.get("RESUME_DATE")
                    or row.get("PREDICT_RESUME_DATE")
                    or row.get("end")
                    or row.get("复牌时间")
                ),
                "reason": row.get("SUSPEND_REASON")
                or row.get("reason")
                or row.get("停牌事项说明")
                or "",
                "duration": row.get("SUSPEND_DAYS")
                or row.get("SUSPEND_TIME")
                or row.get("停牌期限"),
                "exchange": row.get("MARKET") or row.get("exchange") or row.get("所属市场") or "",
                "source": source,
            }
        )
    return out
