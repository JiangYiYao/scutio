"""Current A-share listing directories, independent of quote availability.

AKShare 1.18.94 source audit: ``akshare/stock/stock_info.py``. SSE requests
one page of 10,000 rows; SZSE downloads the entire A-share XLSX; BSE walks
``totalPages`` internally. The adapters discard SSE/BSE pagination evidence,
so successful requests alone cannot certify completeness for those exchanges.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from scutio_data._providers.akshare import client
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import require_a_share
from scutio_data._runtime.timeouts import operation

__all__ = ["stock_universe"]


_DIRECTORIES = (
    ("sh_main", "sh", "stock_info_sh_name_code", {"symbol": "主板A股"}, "主板"),
    ("sh_star", "sh", "stock_info_sh_name_code", {"symbol": "科创板"}, "科创板"),
    ("sz", "sz", "stock_info_sz_name_code", {"symbol": "A股列表"}, None),
    ("bj", "bj", "stock_info_bj_name_code", {}, "北交所"),
)
_SOURCE_URLS = {
    "sh": "https://www.sse.com.cn/assortment/stock/list/share/",
    "sz": "https://www.szse.cn/market/product/stock/list/index.html",
    "bj": "https://www.bse.cn/nq/listedcompany.html",
}
_PAGINATION = {
    "sh": {"mode": "single_page", "requested_page_size": 10000, "requested_pages": [1]},
    "sz": {"mode": "full_xlsx_export", "paginated": False},
    "bj": {"mode": "adapter_iterates_totalPages", "first_page": 0},
}


def _timestamp(value):
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _listing(row, exchange, board, as_of, source):
    code_field, name_field, date_field = (
        ("A股代码", "A股简称", "A股上市日期")
        if exchange == "sz"
        else ("证券代码", "证券简称", "上市日期")
    )
    raw = row.get(code_field)
    if isinstance(raw, bool) or not re.fullmatch(r"\d{1,6}(?:\.0)?", str(raw)):
        raise ValueError("invalid_listing_code")
    code = str(raw).split(".")[0].zfill(6)
    _, prefix, code = require_a_share(exchange + code, "stock_universe")
    # STAR's directory also includes CDRs (689xxx), which are not A shares.
    if exchange == "sh" and code.startswith("689"):
        return None
    name = row.get(name_field)
    if not isinstance(name, str) or not name.strip() or name.strip().lower() in {"nan", "none"}:
        raise ValueError("missing_listing_name")
    listed_date = str(row.get(date_field) or "")[:10] or None
    return {
        "symbol": prefix + code,
        "code": code,
        "name": name.strip(),
        "exchange": prefix,
        "asset_type": "a-share",
        "listing_status": "listed",
        "trading_status": None,
        "as_of": as_of,
        "source": source,
        "source_url": _SOURCE_URLS[exchange],
        "board": row.get("板块") or board,
        "listed_date": listed_date,
    }


def _directory(key, exchange, function, params, board):
    meta = {
        "source": "akshare:" + function,
        "source_url": _SOURCE_URLS[exchange],
        "parameters": dict(params),
        "pagination": {
            **_PAGINATION[exchange],
            "observed_pages": None,
            "upstream_total": None,
        },
        "rows_received": 0,
        "returned_count": 0,
        "invalid_count": 0,
        "rejected_rows": [],
        "rejected_rows_limit": 20,
        "duplicate_count": 0,
        "scope_excluded_count": 0,
        "scope_exclusions": {},
        "source_complete": None,
        "independently_verified": False,
        "as_of": None,
        "errors": [],
    }
    items = {}
    try:
        rows, snapshot = client.fetch(function, _snapshot=True, _timeout_seconds=45, **params)
        meta.update(snapshot)
        meta["as_of"] = _timestamp(snapshot["snapshot_retrieved_at"])
        if not isinstance(rows, list) or not rows:
            raise ValueError("empty_listing_directory")
        meta["rows_received"] = len(rows)
        conflicts = set()
        for row_number, row in enumerate(rows, start=1):
            try:
                if not isinstance(row, dict):
                    raise ValueError("invalid_listing_row")
                item = _listing(row, exchange, board, meta["as_of"], meta["source"])
            except ValueError as exc:
                meta["invalid_count"] += 1
                if len(meta["rejected_rows"]) < meta["rejected_rows_limit"]:
                    field = "A股代码" if exchange == "sz" else "证券代码"
                    meta["rejected_rows"].append(
                        {
                            "row": row_number,
                            "code": str(row.get(field, ""))[:32] if isinstance(row, dict) else None,
                            "error": str(exc),
                        }
                    )
                continue
            if item is None:
                meta["scope_excluded_count"] += 1
                meta["scope_exclusions"]["cdr"] = meta["scope_excluded_count"]
                continue
            symbol = item["symbol"]
            if symbol in items or symbol in conflicts:
                meta["duplicate_count"] += 1
                if symbol in conflicts or items[symbol]["name"] != item["name"]:
                    conflicts.add(symbol)
                    items.pop(symbol, None)
                continue
            items[symbol] = item
        if meta["invalid_count"]:
            meta["errors"].append("invalid_listing_rows")
        if meta["duplicate_count"]:
            meta["errors"].append("duplicate_listing_rows")
        if not items:
            meta["errors"].append("no_valid_a_share_listings")
        if exchange == "sh" and len(rows) >= 10000:
            meta["errors"].append("listing_page_limit_reached")
        # The XLSX response is the complete source export. This does not assert
        # an independent cross-check or reconstruct a historical universe.
        meta["source_complete"] = False if meta["errors"] else (True if exchange == "sz" else None)
    except Exception as exc:
        meta["errors"].append(str(exc))
        meta["source_complete"] = False
    meta["returned_count"] = len(items)
    meta["completeness"] = (
        "partial" if meta["errors"] else ("complete" if meta["source_complete"] else "unknown")
    )
    return list(items.values()), meta


@operation("batch")
def stock_universe(market="a"):
    """Return the current listed A-share universe from SSE, SZSE and BSE.

    Includes ST and suspended listings; quote availability is never a filter.
    Excludes B shares, funds, indices, CDRs and delisted securities. ``as_of``
    records directory retrieval time (including cache age), not a historical
    membership date. Each exact directory request uses the shared one-hour
    snapshot cache. ``is_complete=None`` means unverified; ``False`` means a
    known gap. Partial successful exchanges remain available in ``items``.
    """
    market = str(market).strip().lower()
    if market != "a":
        return result_list_err(
            "unsupported_market: stock_universe supports a only",
            source="exchange_listing_directories",
            market=market,
            coverage={},
            is_complete=False,
            completeness="partial",
            partial=False,
        )
    coverage = {
        exchange: {
            "source_url": _SOURCE_URLS[exchange],
            "parts": {},
            "returned_count": 0,
            "missing_count": None,
            "independently_verified": False,
        }
        for exchange in ("sh", "sz", "bj")
    }
    merged = {}
    errors = {}
    for key, exchange, function, params, board in _DIRECTORIES:
        items, part = _directory(key, exchange, function, params, board)
        coverage[exchange]["parts"][key] = part
        for item in items:
            if item["symbol"] in merged:
                part["errors"].append("duplicate_symbol_across_directories")
                part["source_complete"] = False
                part["completeness"] = "partial"
                continue
            merged[item["symbol"]] = item
        if part["errors"]:
            errors[key] = list(part["errors"])
    for exchange, summary in coverage.items():
        parts = list(summary["parts"].values())
        summary["returned_count"] = sum(item["exchange"] == exchange for item in merged.values())
        states = [part["source_complete"] for part in parts]
        complete = False if False in states else (True if all(states) else None)
        summary.update(
            is_complete=complete,
            source_complete=complete,
            completeness=(
                "partial" if complete is False else ("complete" if complete else "unknown")
            ),
            as_of=max((part["as_of"] or "" for part in parts), default="") or None,
        )
    complete = False if errors else None
    payload = {
        "market": "a",
        "universe_type": "exchange_listing_directory",
        "as_of": max((item["as_of"] for item in merged.values()), default=None),
        "as_of_basis": "directory_retrieved_at",
        "historical_membership": False,
        "is_complete": complete,
        "completeness": "partial" if errors else "unknown",
        "partial": bool(merged) and bool(errors),
        "coverage": coverage,
        "errors": errors,
        "returned_count": len(merged),
        "warning": ("Known listing gaps; inspect coverage and errors. " if errors else "")
        + "SSE/BSE totals and pagination evidence are not exposed by AKShare; "
        + "completeness is unverified.",
    }
    items = sorted(merged.values(), key=lambda row: row["symbol"])
    if not items:
        return result_list_err(
            "no_valid_listing_directories", source="exchange_listing_directories", **payload
        )
    return result_list(items, source="exchange_listing_directories", **payload)
