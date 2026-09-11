"""命名宏观序列、来源选择、时效校验与一致预期历史。"""

from __future__ import annotations

import re
from datetime import datetime
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from scutio_data._providers.akshare.macro import CN_DESCRIPTIONS, cn_series
from scutio_data._runtime.environment import CN_TZ
from scutio_data._runtime.parsing import finite_number
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.timeouts import operation

# 中国序列 → 金十 attr_id（单位与东财主序列对齐的才挂备胎；社融金十无对应表）


# 美国序列 → 金十 attr_id


# SHIBOR 期限与 AKShare 参数。
_SHIBOR_TENORS = {"on": "隔夜", "1w": "1周", "1m": "1月", "3m": "3月", "1y": "1年"}


# rates_snapshot 默认拉取的 SHIBOR 期限（其余仍可走全表扩展）
_SHIBOR_SNAPSHOT_KEYS = ("on", "1w", "1m", "3m", "1y")


# 美国：东财 RPT_ECONOMICVALUE_USA 的 INDICATOR_ID
# 名称随上游微调；以 ID 为准。VALUE 为空时跳过该行（常见为未发布占位）。
_US_INDICATORS: Dict[str, Tuple[str, str, str]] = {
    # name -> (indicator_id, unit, description)
    "us_cpi_yoy": ("EMG00000733", "pct", "美国 CPI 当月同比 %（非季调）"),
    "us_core_cpi_yoy": ("EMG00000746", "pct", "美国核心 CPI 当月同比 %（季调）"),
    "us_cpi_mom": ("EMG00000770", "pct", "美国 CPI 环比 %（季调）"),
    "us_unemployment": ("EMG00001039", "pct", "美国失业率 %（季调）"),
    "us_nfp": ("EMG00152118", "k_persons", "美国新增非农就业（千人，季调）"),
    "us_ism_pmi": ("EMG00002790", "index", "美国 ISM 制造业 PMI（季调）"),
    "us_ism_services": ("EMG00002791", "index", "美国 ISM 服务业 PMI（季调）"),
    "us_fed_funds_upper": ("EMG00342250", "pct", "联邦基金利率目标上限 %"),
    "us_fed_funds_lower": ("EMG00358536", "pct", "联邦基金利率目标下限 %"),
    "us_gdp_qoq": ("EMG00159633", "pct", "美国 GDP 环比折年率 %（2017 价，季调）"),
    "us_retail_sales_mom": ("EMG00003721", "pct", "美国零售销售环比 %（季调，初步）"),
    "us_michigan": ("EMG00002846", "index", "密歇根大学消费者信心（初值）"),
    "us_cb_confidence": ("EMG00002847", "index", "谘商会消费者信心指数"),
    "us_ppi_core_yoy": ("EMG00177799", "pct", "美国核心 PPI 同比 %（非季调）"),
}


def _date_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    # 2026-07-30 00:00:00 / 2026年07月份
    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月]?(\d{1,2})?", s)
    if m:
        y, mo = m.group(1), int(m.group(2))
        d = int(m.group(3) or 1)
        return "%04d-%02d-%02d" % (int(y), mo, d)
    if "年" in s and "月" in s:
        m2 = re.search(r"(\d{4})年(\d{1,2})月", s)
        if m2:
            return "%04d-%02d-01" % (int(m2.group(1)), int(m2.group(2)))
    return s[:10]


def map_lpr_rows(rows: Sequence[dict], *, limit: int = 20) -> List[dict]:
    out: List[dict] = []
    for row in list(rows)[: max(1, limit)]:
        out.append(
            {
                "date": _date_str(row.get("TRADE_DATE") or row.get("REPORT_DATE")),
                "lpr_1y": finite_number(row.get("LPR1Y")),
                "lpr_5y": finite_number(row.get("LPR5Y")),
                "loan_1y": finite_number(row.get("RATE_1")),
                "loan_5y_plus": finite_number(row.get("RATE_2")),
                "name": "lpr",
                "unit": "pct",
                "freq": "D",
                "source": "eastmoney_RPTA_WEB_RATE",
            }
        )
    return out


def map_us_indicator_rows(
    rows: Sequence[dict],
    *,
    series_name: str,
    unit: str = "pct",
    freq: str = "M",
    source: str = "eastmoney_RPT_ECONOMICVALUE_USA",
    limit: int = 36,
) -> List[dict]:
    """映射 ``RPT_ECONOMICVALUE_USA`` 行；跳过 VALUE 为空的占位行。"""
    out: List[dict] = []
    for row in list(rows):
        val = finite_number(row.get("VALUE"))
        if val is None:
            continue
        out.append(
            {
                "date": _date_str(row.get("REPORT_DATE") or row.get("PUBLISH_DATE")),
                "period": row.get("REPORT_DATE_CH") or "",
                "name": series_name,
                "value": val,
                "prev_value": finite_number(row.get("PRE_VALUE")),
                "indicator_id": row.get("INDICATOR_ID") or "",
                "indicator_name": row.get("INDICATOR_NAME") or "",
                "unit": unit,
                "freq": freq,
                "market": "US",
                "source": source,
            }
        )
        if len(out) >= max(1, limit):
            break
    return out


def map_social_financing_rows(rows: Sequence[dict], *, limit: int = 36) -> List[dict]:
    """映射商务部社融增量 JSON 行（字段名按接口 key，非列序）。

    上游字段（亿元）：``tiosfs`` 社融增量，``rmblaon`` 人民币贷款，
    ``forcloan`` 外币贷款，``entrustloan`` 委托贷款，``trustloan`` 信托贷款，
    ``ndbab`` 未贴现银票，``bibae`` 企业债券，``sfinfe`` 非金融企业境内股票融资。
    """
    parsed: List[Tuple[str, dict]] = []
    for row in rows:
        raw_date = str(row.get("date") or "").strip()
        if len(raw_date) == 6 and raw_date.isdigit():
            date = "%s-%s-01" % (raw_date[:4], raw_date[4:6])
            period = "%s年%s月" % (raw_date[:4], int(raw_date[4:6]))
        else:
            date = _date_str(raw_date)
            period = raw_date
        val = finite_number(row.get("tiosfs"))
        if not date and val is None:
            continue
        parsed.append(
            (
                date,
                {
                    "date": date,
                    "period": period,
                    "name": "social_financing",
                    "value": val,
                    "unit": "yi_cny",
                    "freq": "M",
                    "market": "CN",
                    "source": "mofcom_shrzgm",
                    "rmb_loan": finite_number(row.get("rmblaon")),
                    "fx_loan": finite_number(row.get("forcloan")),
                    "entrusted_loan": finite_number(row.get("entrustloan")),
                    "trust_loan": finite_number(row.get("trustloan")),
                    "bank_acceptance": finite_number(row.get("ndbab")),
                    "corp_bond": finite_number(row.get("bibae")),
                    "equity_financing": finite_number(row.get("sfinfe")),
                },
            )
        )
    # 新→旧
    parsed.sort(key=lambda x: x[0] or "", reverse=True)
    return [p[1] for p in parsed[: max(1, limit)]]


def map_jin10_ec_rows(
    values: Sequence[Sequence[Any]],
    *,
    series_name: str,
    unit: str = "pct",
    freq: str = "M",
    market: str = "CN",
    limit: int = 36,
) -> List[dict]:
    """映射金十 ``list_v2`` 的 values：``[日期, 今值, 预测值, 前值]``。"""
    out: List[dict] = []
    for row in values:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        val = finite_number(row[1])
        if val is None:
            continue
        out.append(
            {
                "date": _date_str(row[0]),
                "period": str(row[0] or ""),
                "name": series_name,
                "value": val,
                "forecast": finite_number(row[2]) if len(row) > 2 else None,
                "prev_value": finite_number(row[3]) if len(row) > 3 else None,
                "unit": unit,
                "freq": freq,
                "market": market,
                "source": "jin10_ec",
            }
        )
    # AKShare returns Jin10 history oldest first. Select the latest published
    # observations only after sorting the complete response.
    out.sort(key=lambda item: item["date"], reverse=True)
    return out[: max(1, limit)]


def _fetch_series_social_financing(limit):
    from scutio_data._providers.akshare.client import fetch

    mapping = {
        "月份": "date",
        "社会融资规模增量": "tiosfs",
        "其中-人民币贷款": "rmblaon",
        "其中-委托贷款外币贷款": "forcloan",
        "其中-委托贷款": "entrustloan",
        "其中-信托贷款": "trustloan",
        "其中-未贴现银行承兑汇票": "ndbab",
        "其中-企业债券": "bibae",
        "其中-非金融企业境内股票融资": "sfinfe",
    }
    rows = fetch("macro_china_shrzgm")
    items = map_social_financing_rows(
        [{v: row.get(k) for k, v in mapping.items()} for row in rows], limit=limit
    )
    for item in items:
        item["source"] = "akshare_mofcom_shrzgm"
    return items


def consensus_history(series_name: str, *, limit: int) -> list[dict]:
    from scutio_data._providers.akshare.client import fetch

    spec = CONSENSUS_SPECS[series_name]
    rows = fetch(spec["function"])
    values = [[r.get("日期"), r.get("今值"), r.get("预测值"), r.get("前值")] for r in rows]
    items = map_jin10_ec_rows(
        values,
        series_name=series_name,
        unit=spec["unit"],
        market=spec["market"],
        freq=spec["freq"],
        limit=limit,
    )
    for item in items:
        item.update(source="akshare_jin10_ec", date_basis="release_date")
        if series_name == "us_nfp":
            for key in ("value", "forecast", "prev_value"):
                item[key] = item[key] * 10 if item.get(key) is not None else None
            item["source_unit"] = "wan_persons"
        item["stale"] = _macro_stale(item)
        item["partial"] = item["stale"]
    if not items:
        raise ValueError("AKShare consensus series has no actual values")
    return items


_US_AK_FUNCTIONS = {
    "us_cpi_yoy": "macro_usa_cpi_yoy",
    "us_cpi_mom": "macro_usa_cpi_monthly",
    "us_unemployment": "macro_usa_unemployment_rate",
    "us_nfp": "macro_usa_non_farm",
    "us_ism_pmi": "macro_usa_ism_pmi",
    "us_ism_services": "macro_usa_ism_non_pmi",
    "us_gdp_qoq": "macro_usa_gdp_monthly",
    "us_retail_sales_mom": "macro_usa_retail_sales",
    "us_michigan": "macro_usa_michigan_consumer_sentiment",
    "us_cb_confidence": "macro_usa_cb_consumer_confidence",
}


def _make_us_fetcher(series_name, indicator_id, unit):
    def run(limit):
        from scutio_data._providers.akshare.client import fetch
        from scutio_data._providers.akshare.errors import AKShareError

        function = _US_AK_FUNCTIONS.get(series_name)
        if function == "macro_usa_cpi_yoy":
            rows = fetch(function)
            rows.sort(key=lambda row: str(row.get("时间") or ""), reverse=True)
            return [
                {
                    "date": _date_str(r.get("时间")),
                    "published_date": _date_str(r.get("发布日期")),
                    "period": _date_str(r.get("时间")),
                    "name": series_name,
                    "value": finite_number(r.get("现值")),
                    "prev_value": finite_number(r.get("前值")),
                    "date_basis": "observation_period",
                    "unit": unit,
                    "freq": "M",
                    "market": "US",
                    "source": "akshare_" + function,
                }
                for r in rows
                if finite_number(r.get("现值")) is not None
            ][: max(1, int(limit))]
        if function:
            # These AKShare adapters fetch decades of Jin10 history. A failed
            # historical page must not hide the equivalent current EM series.
            try:
                rows = fetch(function)
            except AKShareError:
                rows = []
            values = [[r.get("日期"), r.get("今值"), r.get("预测值"), r.get("前值")] for r in rows]
            items = map_jin10_ec_rows(
                values, series_name=series_name, unit=unit, market="US", limit=limit
            )
            for item in items:
                item.update(
                    source="akshare_" + function,
                    date_basis="release_date",
                    freq="Q" if series_name == "us_gdp_qoq" else "M",
                )
            if series_name == "us_nfp":
                for item in items:
                    for key in ("value", "forecast", "prev_value"):
                        item[key] = item[key] * 10 if item.get(key) is not None else None
                    item["source_unit"] = "wan_persons"
            if items and not _macro_stale(items[0]):
                return items
            # Verified Jin10 history stops around 2025 for these series. Do not
            # substitute those historical values for the latest economic release.
        from scutio_data._providers.eastmoney_special import us_macro_rows

        return map_us_indicator_rows(
            us_macro_rows(indicator_id, limit),
            series_name=series_name,
            unit=unit,
            freq="Q" if series_name == "us_gdp_qoq" else "M",
            limit=limit,
        )

    return run


# name -> (fetcher, description)  · 中国


# name -> (fetcher, description)  · 美国
US_SERIES_SPECS: Dict[str, Tuple[Callable[[int], List[dict]], str]] = {
    name: (
        _make_us_fetcher(name, iid, unit),
        desc + ("（AKShare）" if name in _US_AK_FUNCTIONS else "（限定数据缺口）"),
    )
    for name, (iid, unit, desc) in _US_INDICATORS.items()
}


SERIES_SPECS = {
    name: (partial(cn_series, name), description)
    for name, description in CN_DESCRIPTIONS.items()
    if name != "social_financing"
}
SERIES_SPECS["social_financing"] = (
    _fetch_series_social_financing,
    CN_DESCRIPTIONS["social_financing"],
)

MACRO_SERIES = frozenset(SERIES_SPECS.keys())


US_MACRO_SERIES = frozenset(US_SERIES_SPECS.keys())


ALL_MACRO_SERIES = frozenset(set(MACRO_SERIES) | set(US_MACRO_SERIES))


def list_macro_series(market: Optional[str] = None) -> List[Dict[str, str]]:
    """列出命名序列。``market``: ``None`` 全部 · ``CN`` / ``US``。"""
    m = (market or "").strip().upper() or None
    out: List[Dict[str, str]] = []
    if m in (None, "CN", "A", "CHINA"):
        for k, (_, desc) in sorted(SERIES_SPECS.items()):
            out.append({"name": k, "description": desc, "market": "CN"})
    if m in (None, "US", "USA"):
        for k, (_, desc) in sorted(US_SERIES_SPECS.items()):
            out.append({"name": k, "description": desc, "market": "US"})
    return out


_DEFAULT_SURPRISE_SERIES = (
    "cpi_yoy",
    "ppi_yoy",
    "pmi_mfg",
    "us_cpi_mom",
    "us_nfp",
    "us_unemployment",
    "us_ism_pmi",
)


def _macro_stale(item):
    """Conservative coverage guard, not a claim about the exact release schedule."""
    threshold = {"M": 120, "Q": 240, "Y": 550}.get(item.get("freq"))
    if item.get("name") == "us_gdp_qoq":
        # EM dates the observation at the first day of the quarter's final
        # month. Six months without a newer quarter misses a normal release.
        threshold = 180
    if threshold is None:
        return False
    try:
        age = (
            datetime.now(CN_TZ).date()
            - datetime.strptime(str(item.get("date"))[:10], "%Y-%m-%d").date()
        ).days
        return age > threshold
    except (TypeError, ValueError):
        return True


def _run_named_series(
    key: str,
    specs: Dict[str, Tuple[Callable[[int], List[dict]], str]],
    *,
    api: str,
    limit: int,
) -> dict:
    fetcher, _desc = specs[key]
    try:
        items = fetcher(max(1, int(limit)))
        if not items:
            return result_list_err("empty series %s" % key, source=api)
        if all(x.get("value") is None for x in items):
            return result_list_err(
                "all values null for %s" % key,
                source=items[0].get("source") or key,
                items=items,
            )
        src = (items[0].get("source") if items else None) or key
        return result_list(
            items,
            source=src,
            requested_count=max(1, int(limit)),
            returned_count=len(items),
            partial=len(items) < max(1, int(limit))
            or _macro_stale(items[0])
            or any(row.get("partial") for row in items),
            stale=_macro_stale(items[0]),
            data_end=items[0].get("date"),
            data_start=items[-1].get("date"),
        )
    except Exception as exc:
        return result_list_err(str(exc), source=key)


@operation("history")
def cn_macro_series(name: str, limit: int = 36) -> dict:
    """命名**中国**宏观序列。见 :data:`MACRO_SERIES` / :func:`list_macro_series`。"""
    key = str(name or "").strip().lower()
    if key not in SERIES_SPECS:
        return result_list_err(
            "unknown CN series %r; choose from %s" % (name, ",".join(sorted(MACRO_SERIES))),
            source="cn_macro_series",
        )
    return _run_named_series(key, SERIES_SPECS, api="cn_macro_series", limit=limit)


@operation("history")
def us_macro_series(name: str, limit: int = 36) -> dict:
    """命名**美国**宏观序列。见 :data:`US_MACRO_SERIES` / :func:`list_macro_series`。"""
    key = str(name or "").strip().lower()
    if key not in US_SERIES_SPECS:
        return result_list_err(
            "unknown US series %r; choose from %s" % (name, ",".join(sorted(US_MACRO_SERIES))),
            source="us_macro_series",
        )
    return _run_named_series(key, US_SERIES_SPECS, api="us_macro_series", limit=limit)


@operation("history")
def macro_series(name: str, limit: int = 36) -> dict:
    """统一命名序列：按名路由 CN / US（``us_*`` → 美国，其余优先中国）。"""
    key = str(name or "").strip().lower()
    if key in US_SERIES_SPECS or key.startswith("us_"):
        return us_macro_series(key, limit=limit)
    if key in SERIES_SPECS:
        return cn_macro_series(key, limit=limit)
    return result_list_err(
        "unknown series %r; see list_macro_series()" % name,
        source="macro_series",
    )


CONSENSUS_SPECS = {
    "cpi_yoy": {"function": "macro_china_cpi_yearly", "unit": "pct", "market": "CN", "freq": "M"},
    "ppi_yoy": {"function": "macro_china_ppi_yearly", "unit": "pct", "market": "CN", "freq": "M"},
    "pmi_mfg": {"function": "macro_china_pmi_yearly", "unit": "index", "market": "CN", "freq": "M"},
    "pmi_non_mfg": {
        "function": "macro_china_non_man_pmi",
        "unit": "index",
        "market": "CN",
        "freq": "M",
    },
    "m2_yoy": {"function": "macro_china_m2_yearly", "unit": "pct", "market": "CN", "freq": "M"},
    "export_yoy": {
        "function": "macro_china_exports_yoy",
        "unit": "pct",
        "market": "CN",
        "freq": "M",
    },
    "import_yoy": {
        "function": "macro_china_imports_yoy",
        "unit": "pct",
        "market": "CN",
        "freq": "M",
    },
    "industrial_yoy": {
        "function": "macro_china_industrial_production_yoy",
        "unit": "pct",
        "market": "CN",
        "freq": "M",
    },
    "forex_reserves": {
        "function": "macro_china_fx_reserves_yearly",
        "unit": "yi_usd",
        "market": "CN",
        "freq": "M",
    },
    "us_cpi_mom": {"function": "macro_usa_cpi_monthly", "unit": "pct", "market": "US", "freq": "M"},
    "us_unemployment": {
        "function": "macro_usa_unemployment_rate",
        "unit": "pct",
        "market": "US",
        "freq": "M",
    },
    "us_nfp": {"function": "macro_usa_non_farm", "unit": "k_persons", "market": "US", "freq": "M"},
    "us_ism_pmi": {"function": "macro_usa_ism_pmi", "unit": "index", "market": "US", "freq": "M"},
    "us_gdp_qoq": {"function": "macro_usa_gdp_monthly", "unit": "pct", "market": "US", "freq": "Q"},
    "us_retail_sales_mom": {
        "function": "macro_usa_retail_sales",
        "unit": "pct",
        "market": "US",
        "freq": "M",
    },
}
