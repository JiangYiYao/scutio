"""东财公司业绩预告、快报的源查询与字段映射。"""

from __future__ import annotations

from typing import Iterable, List

from scutio_data._providers import eastmoney
from scutio_data._runtime.dates import iso_date as iso_date


def map_performance_update_rows(rows: Iterable[dict], *, kind: str) -> List[dict]:
    out = []
    for row in rows or []:
        common = {
            "kind": kind,
            "code": str(row.get("SECURITY_CODE") or "").zfill(6),
            "name": row.get("SECURITY_NAME_ABBR") or "",
            "report_date": iso_date(row.get("REPORT_DATE")),
            "notice_date": iso_date(row.get("NOTICE_DATE") or row.get("UPDATE_DATE")),
            "industry": row.get("PUBLISHNAME") or "",
            "source": "eastmoney_performance_%s" % kind,
        }
        if kind == "forecast":
            common.update(
                {
                    "metric": row.get("PREDICT_FINANCE") or "",
                    "amount_low": row.get("PREDICT_AMT_LOWER"),
                    "amount_high": row.get("PREDICT_AMT_UPPER"),
                    "change_pct_low": row.get("ADD_AMP_LOWER"),
                    "change_pct_high": row.get("ADD_AMP_UPPER"),
                    "forecast_mid": row.get("FORECAST_JZ"),
                    "forecast_type": row.get("PREDICT_TYPE") or "",
                    "content": row.get("PREDICT_CONTENT") or "",
                    "reason": row.get("CHANGE_REASON_EXPLAIN") or "",
                    "prior_period": row.get("PREYEAR_SAME_PERIOD"),
                    "is_latest": row.get("IS_LATEST"),
                }
            )
        else:
            common.update(
                {
                    "eps": row.get("BASIC_EPS"),
                    "revenue": row.get("TOTAL_OPERATE_INCOME"),
                    "revenue_prior": row.get("TOTAL_OPERATE_INCOME_SQ"),
                    "revenue_yoy_pct": row.get("YSTZ"),
                    "net_profit": row.get("PARENT_NETPROFIT"),
                    "net_profit_prior": row.get("PARENT_NETPROFIT_SQ"),
                    "net_profit_yoy_pct": row.get("JLRTBZCL"),
                    "book_value_per_share": row.get("PARENT_BVPS"),
                    "roe_pct": row.get("WEIGHTAVG_ROE"),
                    "data_type": row.get("DATATYPE") or "",
                    "is_latest": row.get("ISNEW"),
                }
            )
        out.append(common)
    return out


def _forecast_page(payload):
    if not isinstance(payload, dict):
        raise ValueError("forecast response must be an object")
    result = payload.get("result")
    if payload.get("success") is False and payload.get("code") == 9201 and result is None:
        return [], 0, 0
    if payload.get("success") is not True or payload.get("code") != 0:
        raise ValueError("forecast provider rejected the request")
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise ValueError("forecast response data missing")
    count, pages = result.get("count"), result.get("pages")
    if (
        type(count) is not int
        or type(pages) is not int
        or count < 0
        or pages < 0
        or (count > 0 and pages == 0)
    ):
        raise ValueError("forecast pagination metadata invalid")
    if pages > 50:
        raise ValueError("forecast result exceeds the supported 50-page window")
    return result["data"], count, pages


def _performance_rows(report_date: str, kind: str, code: str = "") -> List[dict]:
    if kind == "express":
        from scutio_data._providers.akshare.client import fetch

        rows = fetch("stock_yjkb_em", date=report_date.replace("-", ""))
        mapping = {
            "股票代码": "SECURITY_CODE",
            "股票简称": "SECURITY_NAME_ABBR",
            "公告日期": "NOTICE_DATE",
            "所处行业": "PUBLISHNAME",
            "每股收益": "BASIC_EPS",
            "营业收入-营业收入": "TOTAL_OPERATE_INCOME",
            "营业收入-去年同期": "TOTAL_OPERATE_INCOME_SQ",
            "营业收入-同比增长": "YSTZ",
            "净利润-净利润": "PARENT_NETPROFIT",
            "净利润-去年同期": "PARENT_NETPROFIT_SQ",
            "净利润-同比增长": "JLRTBZCL",
            "每股净资产": "PARENT_BVPS",
            "净资产收益率": "WEIGHTAVG_ROE",
        }
        raw = [
            {
                **{target: row.get(source) for source, target in mapping.items()},
                "REPORT_DATE": report_date,
            }
            for row in rows
            if not code or str(row.get("股票代码") or "").zfill(6) == code
        ]
        items = map_performance_update_rows(raw, kind=kind)
        for item in items:
            item.update(
                source="akshare_eastmoney_performance_express",
                coverage={"is_latest": False, "data_type": False},
            )
        return items
    report_name, sort_columns = _PERFORMANCE_REPORTS[kind]
    filter_str = "(REPORT_DATE='%s')" % report_date
    if code:
        filter_str += '(SECURITY_CODE="%s")' % code
    params = {
        "sortColumns": sort_columns,
        "sortTypes": ",".join("-1" for _ in sort_columns.split(",")),
        "pageSize": "500",
        "pageNumber": "1",
        "reportName": report_name,
        "columns": "ALL",
        "filter": filter_str,
    }
    endpoint = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    first = eastmoney.em_get(
        endpoint,
        params=params,
        headers={"Referer": "https://data.eastmoney.com/bbsj/"},
        timeout=20,
    ).json()
    rows, expected_count, pages = _forecast_page(first)
    rows = list(rows)
    for page in range(2, pages + 1):
        params["pageNumber"] = str(page)
        payload = eastmoney.em_get(
            endpoint,
            params=params,
            headers={"Referer": "https://data.eastmoney.com/bbsj/"},
            timeout=20,
        ).json()
        page_rows, page_count, page_total = _forecast_page(payload)
        if (page_count, page_total) != (expected_count, pages):
            raise ValueError("forecast pagination changed during collection")
        rows.extend(page_rows)
    identities = set()
    for row in rows:
        identity = str(row.get("SECURITY_CODE") or "") if isinstance(row, dict) else ""
        if (
            not identity.isdigit()
            or len(identity) != 6
            or (code and identity != code)
            or iso_date(row.get("REPORT_DATE")) != report_date
        ):
            raise ValueError("forecast security or report period mismatch")
        metric = row.get("PREDICT_FINANCE_CODE") or row.get("PREDICT_FINANCE")
        if not metric:
            raise ValueError("forecast metric identity missing")
        key = (identity, row.get("NOTICE_DATE"), metric)
        if key in identities:
            raise ValueError("forecast pagination returned duplicate records")
        identities.add(key)
    if len(rows) != expected_count:
        raise ValueError("forecast pagination returned an incomplete result")
    return map_performance_update_rows(rows, kind=kind)


_PERFORMANCE_REPORTS = {
    "forecast": (
        "RPT_PUBLIC_OP_NEWPREDICT",
        "NOTICE_DATE,SECURITY_CODE,PREDICT_FINANCE_CODE",
    ),
}
