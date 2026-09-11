"""东财美国宏观指标适配；范围由能力契约限制。"""

from scutio_data._providers import eastmoney

# AKShare has monthly core CPI/PPI, but these requests require yearly changes.
# Federal-funds candidate lacks explicit upper/lower identity. The other nine
# Jin10 series stop around 2025; caller permits them here only after stale/empty
# candidate data, not arbitrary network failure. See audit for reproducible dates.
_US_MACRO_IDS = frozenset(
    (
        "EMG00000746",
        "EMG00342250",
        "EMG00358536",
        "EMG00177799",
        "EMG00000770",
        "EMG00001039",
        "EMG00152118",
        "EMG00002790",
        "EMG00002791",
        "EMG00159633",
        "EMG00003721",
        "EMG00002846",
        "EMG00002847",
    )
)


def us_macro_rows(indicator_id, limit):
    if indicator_id not in _US_MACRO_IDS:
        raise ValueError("US macro gap does not permit this indicator")
    payload = eastmoney.em_get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "reportName": "RPT_ECONOMICVALUE_USA",
            "columns": "ALL",
            "pageSize": min(500, max(15, int(limit) * 3)),
            "pageNumber": 1,
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "filter": '(INDICATOR_ID="%s")' % indicator_id,
        },
        timeout=20,
    ).json()
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise ValueError("US macro gap source missing result")
    rows = result["data"]
    if any(str(row.get("INDICATOR_ID")) != indicator_id for row in rows):
        raise ValueError("US macro indicator mismatch")
    return rows
