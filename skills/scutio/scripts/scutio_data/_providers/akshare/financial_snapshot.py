"""Report-period cross-company features from AKShare 1.18.94's stock_yjbb_em.

The official adapter pages RPT_LICO_FN_CPD with a REPORTDATE filter. It drops
REPORTDATE, SECURITY_TYPE, exchange and listing status; its rows include OTC
issuers. Never use this table as a listing universe. The displayed latest
announcement date is actually the upstream UPDATE_DATE, not first disclosure.
"""

from __future__ import annotations

import math
import re
from datetime import date

from scutio_data._providers.akshare import client
from scutio_data._runtime.results import result_list
from scutio_data._runtime.symbols import require_a_share, validate_identity

FUNCTION = "stock_yjbb_em"
SOURCE = "akshare_eastmoney_financial_snapshot"
TIME_BASIS = "retrospective_current_materials"

# Units and output titles are documented at
# https://akshare.akfamily.xyz/data/stock/stock.html (stock_yjbb_em). Native IDs were
# checked against RPT_LICO_FN_CPD on 2026-09-22; net profit is attributable to
# the parent, ROE is weighted, and EPS is basic EPS.
FIELD_CATALOG = {
    "revenue": {
        "source_field": "营业总收入-营业总收入",
        "native_field": "TOTAL_OPERATE_INCOME",
        "unit": "CNY",
        "basis": "total_operating_revenue_ytd",
    },
    "net_profit": {
        "source_field": "净利润-净利润",
        "native_field": "PARENT_NETPROFIT",
        "unit": "CNY",
        "basis": "profit_attributable_to_parent_ytd",
    },
    "revenue_yoy": {
        "source_field": "营业总收入-同比增长",
        "native_field": "YSTZ",
        "unit": "pct",
        "basis": "provider_reported_base_unknown",
    },
    "net_profit_yoy": {
        "source_field": "净利润-同比增长",
        "native_field": "SJLTZ",
        "unit": "pct",
        "basis": "provider_reported_base_unknown",
    },
    "roe": {
        "source_field": "净资产收益率",
        "native_field": "WEIGHTAVG_ROE",
        "unit": "pct",
        "basis": "weighted_return_on_equity_ytd_not_annualized",
    },
    "gross_margin": {
        "source_field": "销售毛利率",
        "native_field": "XSMLL",
        "unit": "pct",
        "basis": "provider_reported_sales_gross_margin_ytd",
    },
    "eps": {
        "source_field": "每股收益",
        "native_field": "BASIC_EPS",
        "unit": "CNY/share",
        "basis": "basic_earnings_per_share_ytd",
    },
    "operating_cashflow_per_share": {
        "source_field": "每股经营现金流量",
        "native_field": "MGJYXJJE",
        "unit": "CNY/share",
        "basis": "operating_cashflow_per_share_ytd",
    },
    "net_assets_per_share": {
        "source_field": "每股净资产",
        "native_field": "BPS",
        "unit": "CNY/share",
        "basis": "net_assets_per_share_at_report_date",
    },
}


def normalize_report_date(value):
    """Require an explicit elapsed quarter end; never guess a latest period."""
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("report_date must be YYYY-MM-DD or YYYYMMDD")
    day = date.fromisoformat(text)
    if text[5:] not in ("03-31", "06-30", "09-30", "12-31"):
        raise ValueError("report_date must be a quarter end")
    if day < date(2010, 3, 31):
        raise ValueError("stock_yjbb_em supports report_date from 2010-03-31")
    if day > date.today():
        raise ValueError("report_date must not be in the future")
    return text


def _requested_symbols(codes):
    if codes is None:
        return None
    if isinstance(codes, (str, bytes, dict)):
        raise ValueError("codes must be an iterable of A-share codes, not a string or mapping")
    symbols = []
    for code in codes:
        _, exchange, pure = require_a_share(code, "financial_snapshot")
        symbol = exchange + pure
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _number(raw):
    if (
        raw is None
        or isinstance(raw, str)
        and raw.strip().lower()
        in {
            "",
            "-",
            "--",
            "n/a",
            "nan",
            "none",
            "null",
        }
    ):
        return None, "missing"
    if isinstance(raw, bool):
        return None, "invalid_number"
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_number"
    if not math.isfinite(value):
        return None, "nonfinite_number"
    return value, None


def _disclosure_date(raw, report_date):
    if raw in (None, ""):
        return None, "missing"
    text = str(raw)[:10]
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None, "invalid_date"
    if text < report_date:
        return text, "before_report_date"
    if day > date.today():
        return text, "future_provider_update_date"
    return text, None


def _normalize_rows(rows, report_date, requested):
    by_symbol, rejected = {}, []
    duplicate_rows = 0
    for position, raw in enumerate(rows):
        if not isinstance(raw, dict):
            rejected.append({"row_index": position, "reason": "invalid_row"})
            continue
        try:
            code = str(raw.get("股票代码") or "").strip()
            # A lost leading zero is an identity error, never repaired by guessing.
            if not re.fullmatch(r"\d{6}", code):
                raise ValueError("invalid_identity")
            _, exchange, pure = require_a_share(code, "financial_snapshot")
            validate_identity(
                raw,
                exchange + pure,
                fields=("股票代码", "SECURITY_CODE", "SECUCODE", "symbol", "code"),
            )
        except ValueError:
            rejected.append({"row_index": position, "reason": "unsupported_or_invalid_identity"})
            continue
        symbol = exchange + pure
        if requested is not None and symbol not in requested:
            continue
        status = {}
        row = {
            "symbol": symbol,
            "code": pure,
            "exchange": exchange,
            "name": raw.get("股票简称") or None,
            "industry": raw.get("所处行业") or None,
            "report_date": report_date,
            "report_date_basis": "request_filter",
            "period_type": "annual" if report_date.endswith("12-31") else "ytd",
            "currency": "CNY",
            "time_basis": TIME_BASIS,
            "source": SOURCE,
            "identity_basis": "source_code_with_exchange_inferred_from_code_family",
            "listing_status": "unverified",
            "disclosed_at_basis": "provider_update_date_not_first_disclosure",
            "yoy_base_status": "not_provided",
            "field_status": status,
        }
        row["disclosed_at"], issue = _disclosure_date(raw.get("最新公告日期"), report_date)
        if issue:
            status["disclosed_at"] = issue
        for name, spec in FIELD_CATALOG.items():
            key = spec["source_field"]
            row[name], issue = _number(raw.get(key))
            if key not in raw:
                issue = "missing_column"
            if issue:
                status[name] = issue
        # Current AKShare discards this column. Validate it if a future adapter
        # preserves it, rather than silently overriding conflicting evidence.
        if any(
            raw.get(key) is not None and str(raw[key])[:10] != report_date
            for key in ("REPORTDATE", "REPORT_DATE", "报告期")
        ):
            for name in FIELD_CATALOG:
                row[name] = None
                status[name] = "report_date_mismatch"
        if symbol in by_symbol:
            duplicate_rows += 1
            old = by_symbol[symbol]
            for name in (*FIELD_CATALOG, "name", "industry", "disclosed_at"):
                if old[name] != row[name] or old["field_status"].get(name) != status.get(name):
                    old[name] = None
                    old["field_status"][name] = "conflicting_rows"
            continue
        by_symbol[symbol] = row
    order = sorted(by_symbol) if requested is None else requested
    return [by_symbol[symbol] for symbol in order if symbol in by_symbol], rejected, duplicate_rows


def snapshot(report_date, *, codes=None):
    period = normalize_report_date(report_date)
    requested = _requested_symbols(codes)
    if requested == []:
        rows, metadata = [], {}
    else:
        rows, metadata = client.fetch(FUNCTION, date=period.replace("-", ""), _snapshot=True)
    if rows and not any(
        isinstance(row, dict)
        and any(spec["source_field"] in row for spec in FIELD_CATALOG.values())
        for row in rows
    ):
        raise ValueError("financial_snapshot source schema has no recognized financial fields")
    items, rejected, duplicates = _normalize_rows(rows, period, requested)
    seen = {row["symbol"] for row in items}
    missing = [symbol for symbol in requested or [] if symbol not in seen]
    return result_list(
        items,
        source=SOURCE,
        adapter="akshare",
        provider="eastmoney",
        source_function=FUNCTION,
        report_date=period,
        report_date_basis="request_filter",
        period_type="annual" if period.endswith("12-31") else "ytd",
        currency="CNY",
        time_basis=TIME_BASIS,
        field_catalog={key: dict(spec) for key, spec in FIELD_CATALOG.items()},
        partial=True,
        complete=False,
        coverage={
            "scope": "source_report_rows_not_listing_universe",
            "listing_membership_verified": False,
            "can_include_otc_and_delisted": True,
            "upstream_count": len(rows),
            "returned_count": len(items),
            "requested_count": None if requested is None else len(requested),
            "rejected_count": len(rejected),
        },
        missing_symbols=missing,
        rejected_rows=rejected,
        duplicate_rows=duplicates,
        warning=(
            "This report table is not a complete listed A-share universe; intersect it with "
            "an independently verified universe. Values are current retrospective report "
            "materials, not point-in-time history. disclosed_at is the provider update date, "
            "not first disclosure. YoY is provider-reported; prior-year base sign is unknown. "
            "pct uses percent units (20 means 20%); YTD values are not TTM or annualized."
        ),
        **metadata,
    )
