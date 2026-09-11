"""AKShare 尚未覆盖的东财行业、策略、宏观和晨会研报列表。"""

from datetime import date, datetime, timedelta

from scutio_data._providers import eastmoney
from scutio_data._runtime.environment import CN_TZ
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.timeouts import operation
from scutio_data._runtime.timeouts import source as source_budget

REPORT_API = "https://reportapi.eastmoney.com/report/list"
REPORT_JG_API = "https://reportapi.eastmoney.com/report/jg"
_JG_QTYPE = {"strategy": "2", "macro": "3", "morning": "4"}


@operation("history")
def industry_reports(industry_code="*", max_pages=5, begin=None, *, end=None):
    """行业研报；默认最近 730 天，coverage 描述分页范围及是否取全。"""
    return _fetch_reports(
        REPORT_API,
        {
            "industryCode": industry_code,
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "qType": "1",
            "code": "",
        },
        source="industry_reports",
        kind="industry",
        max_pages=max_pages,
        page_size=100,
        begin=begin,
        end=end,
    )


@operation("history")
def broker_reports(kind="strategy", max_pages=3, begin=None, page_size=50, *, end=None):
    """策略/宏观/晨报；kind 为 strategy|macro|morning，默认最近 730 天。"""
    kind = str(kind).lower()
    if kind not in _JG_QTYPE:
        return result_list_err(
            "kind must be one of: strategy, macro, morning",
            source="broker_reports",
        )
    return _fetch_reports(
        REPORT_JG_API,
        {"qType": _JG_QTYPE[kind]},
        source="broker_reports",
        kind=kind,
        max_pages=max_pages,
        page_size=page_size,
        begin=begin,
        end=end,
    )


def _query_window(begin, end):
    end_date = date.fromisoformat(end) if end is not None else datetime.now(CN_TZ).date()
    begin_date = date.fromisoformat(begin) if begin is not None else end_date - timedelta(days=730)
    if begin_date > end_date:
        raise ValueError("begin must not be after end")
    return begin_date.isoformat(), end_date.isoformat()


def _read_page(payload):
    """验证响应，避免把错误对象或字段变化当作空研报列表。"""
    if not isinstance(payload, dict):
        raise ValueError("report response must be an object")
    if payload.get("success") is False or payload.get("error"):
        raise ValueError("report API returned an error")
    rows = payload.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("report response data must be a list of objects")
    total = payload.get("TotalPage", payload.get("totalPage"))
    if total is not None:
        if isinstance(total, bool) or not str(total).isdigit():
            raise ValueError("report response has invalid total pages")
        total = int(total)
        if total == 0 and rows:
            raise ValueError("report response has rows but zero total pages")
    return rows, total


@source_budget("history")
def _fetch_reports(url, params, *, source, kind, max_pages, page_size, begin, end):
    try:
        for name, value, maximum in (("max_pages", max_pages, 50), ("page_size", page_size, 100)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}")
        begin, end = _query_window(begin, end)
    except (TypeError, ValueError) as exc:
        return result_list_err(str(exc), source=source)

    records, errors, seen = [], [], set()
    pages_fetched, total_pages, complete = 0, None, False
    for page in range(1, max_pages + 1):
        try:
            response = eastmoney.em_get(
                url,
                params={
                    **params,
                    "pageSize": str(page_size),
                    "pageNo": str(page),
                    "beginTime": begin,
                    "endTime": end,
                },
                headers={"Referer": "https://data.eastmoney.com/"},
                timeout=30,
            )
            response.raise_for_status()
            rows, reported_total = _read_page(response.json())
            if reported_total is not None:
                total_pages = reported_total
            if not rows and total_pages is not None and page < total_pages:
                raise ValueError("empty report page before reported end")
            for row in rows:
                identity = row.get("infoCode") or row.get("encodeUrl")
                if identity:
                    identity = str(identity)
                    if identity in seen:
                        continue
                    seen.add(identity)
                records.append({**row, "_report_kind": kind})
            pages_fetched += 1
            if not rows or (total_pages is not None and page >= total_pages):
                complete = True
                break
        except Exception as exc:
            errors.append({"page": page, "error": str(exc)})
            break

    extra = {
        "provider": "eastmoney",
        "adapter": "direct",
        "partial": bool(errors and records),
        "errors": errors,
        "coverage": {
            "begin": begin,
            "end": end,
            "pages_fetched": pages_fetched,
            "total_pages": total_pages,
            "max_pages": max_pages,
            "page_size": page_size,
            "returned": len(records),
            "complete": complete,
            "truncated": not complete and not errors,
        },
    }
    if errors and not records:
        return result_list_err(errors[0]["error"], source=source, **extra)
    return result_list(records, source=source, **extra)
