"""Feature-driven market screening and the single local condition evaluator."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from functools import partial
from zoneinfo import ZoneInfo

from scutio_data._runtime.parsing import formatted_number as _number
from scutio_data._runtime.results import result_err, result_ok
from scutio_data._runtime.symbols import require_a_share, validate_identity
from scutio_data._runtime.timeouts import RequestCancelled, cancellation_scope, check_cancelled
from scutio_data.batch import fetch_many

__all__ = ["feature_catalog", "screen_records", "screen_market"]


def _field(label, group, unit, **extra):
    return {"label": label, "group": group, "unit": unit, "markets": ["a"], **extra}


_FIELDS = {
    "symbol": _field("完整证券代码", "universe", "text"),
    "code": _field("证券代码", "universe", "text"),
    "name": _field("证券简称", "universe", "text"),
    "exchange": _field("交易所", "universe", "text"),
    "board": _field("上市板块", "universe", "text"),
    "listed_date": _field("上市日期", "universe", "YYYY-MM-DD"),
    "listing_age_days": _field("上市天数", "universe", "day", dependencies=["listed_date"]),
    "price": _field("现价", "quote", "CNY"),
    "last_close": _field("昨收", "quote", "CNY"),
    "change_pct": _field("当日涨跌幅", "quote", "percent"),
    "mcap_yi": _field("总市值", "quote", "CNY 100 million"),
    "float_mcap_yi": _field("流通市值", "quote", "CNY 100 million"),
    "pe_ttm": _field("滚动市盈率", "quote", "multiple"),
    "pb": _field("市净率", "quote", "multiple"),
    "turnover_pct": _field("换手率", "quote", "percent"),
    "amount": _field("当日成交额", "quote", "CNY"),
    "volume": _field("当日成交量", "quote", "share"),
    "vol_ratio": _field("量比", "quote", "multiple"),
    "amplitude_pct": _field("当日振幅", "quote", "percent"),
    "earnings_yield_pct": _field(
        "滚动盈利收益率", "quote", "percent", dependencies=["pe_ttm"], formula="100 / pe_ttm"
    ),
    "revenue": _field("营业总收入", "financial", "CNY"),
    "net_profit": _field("归母净利润", "financial", "CNY"),
    "revenue_yoy": _field(
        "源披露收入同比", "financial", "percent", basis="provider_reported_base_unknown"
    ),
    "net_profit_yoy": _field(
        "源披露归母净利润同比", "financial", "percent", basis="provider_reported_base_unknown"
    ),
    "roe": _field("加权净资产收益率", "financial", "percent"),
    "gross_margin": _field("毛利率", "financial", "percent"),
    "eps": _field("基本每股收益", "financial", "CNY/share"),
    "operating_cashflow_per_share": _field("每股经营现金流", "financial", "CNY/share"),
    "net_assets_per_share": _field("每股净资产", "financial", "CNY/share"),
    "industry": _field("财务列表行业分类", "financial", "text"),
    "net_margin_pct": _field(
        "归母净利润/营业总收入",
        "financial",
        "percent",
        dependencies=["net_profit", "revenue"],
        formula="100 * net_profit / revenue (revenue > 0)",
    ),
    "revenue_growth_pct": _field(
        "正基数收入同比",
        "financial",
        "percent",
        dependencies=["revenue"],
        prior_year=True,
        formula="100 * (revenue / prior_year_revenue - 1), prior_year_revenue > 0",
    ),
    "net_profit_growth_pct": _field(
        "正基数归母净利润同比",
        "financial",
        "percent",
        dependencies=["net_profit"],
        prior_year=True,
        formula="100 * (net_profit / prior_year_net_profit - 1), prior_year_net_profit > 0",
    ),
}
_NUMERIC_OPERATORS = frozenset(("min", "max", "gt", "lt"))
_OPERATORS = _NUMERIC_OPERATORS | {"eq", "contains", "in"}


def feature_catalog():
    """Return executable features, definitions and input requirements, without fetching."""
    items = []
    for name, definition in _FIELDS.items():
        item = {"name": name, **deepcopy(definition)}
        item["requires_report_date"] = item["group"] == "financial"
        if item["group"] == "financial":
            item["period_basis"] = "specified_report_date_ytd_or_annual"
        items.append(item)
    return result_ok(source="feature_catalog", items=items, operators=sorted(_OPERATORS))


def _validate(filters, sort_by, descending, limit):
    if not isinstance(filters, dict) or not filters:
        raise ValueError("explicit nonempty filters are required")
    for field, condition in filters.items():
        if not isinstance(field, str) or not field:
            raise ValueError("filter field must be a nonempty string")
        rule = condition if isinstance(condition, dict) else {"eq": condition}
        if not rule or set(rule) - _OPERATORS:
            raise ValueError("unsupported filter operator")
        for op, value in rule.items():
            if op in _NUMERIC_OPERATORS and (
                not isinstance(value, (int, float)) or _number(value) is None
            ):
                raise ValueError("numeric thresholds must be finite numbers in input units")
            if op == "eq" and (
                value is None
                or isinstance(value, (list, tuple, dict))
                or isinstance(value, float)
                and _number(value) is None
            ):
                raise ValueError("eq requires a nonmissing finite scalar")
            if op == "contains" and not isinstance(value, str):
                raise ValueError("contains must be a string")
            if op == "in" and (
                not isinstance(value, list)
                or not value
                or any(
                    v is None
                    or isinstance(v, (list, dict, tuple))
                    or isinstance(v, float)
                    and _number(v) is None
                    for v in value
                )
            ):
                raise ValueError("in requires a nonempty list of nonmissing scalars")
        if "min" in rule and "max" in rule and rule["min"] > rule["max"]:
            raise ValueError("min must not exceed max")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer or None")
    if sort_by is not None and (not isinstance(sort_by, str) or not sort_by):
        raise ValueError("sort_by must be a numeric field name")
    if type(descending) is not bool:
        raise ValueError("descending must be boolean")


def _evaluate(row, filters):
    """AND with unknowns: any proven false condition can exclude a record."""
    missing = []
    for field, condition in filters.items():
        rule = condition if isinstance(condition, dict) else {"eq": condition}
        value = row.get(field)
        numeric = bool(set(rule) & _NUMERIC_OPERATORS)
        number = _number(value) if numeric else None
        if (
            value is None
            or numeric
            and number is None
            or isinstance(value, float)
            and _number(value) is None
        ):
            missing.append(field)
            continue
        for op, target in rule.items():
            matched = {
                "min": lambda: number >= target,
                "max": lambda: number <= target,
                "gt": lambda: number > target,
                "lt": lambda: number < target,
                "eq": lambda: value == target,
                "contains": lambda: target.casefold() in str(value).casefold(),
                "in": lambda: value in target,
            }[op]()
            if not matched:
                return False, []
    return (None, missing) if missing else (True, [])


def screen_records(records, filters, *, universe, as_of, sort_by=None, descending=True, limit=None):
    """Filter supplied rows in their declared units. This function performs no I/O."""
    _validate(filters, sort_by, descending, limit)
    if not isinstance(universe, str) or not universe.strip():
        raise ValueError("universe must describe the input sample")
    if not isinstance(as_of, str) or not as_of.strip():
        raise ValueError("as_of must describe the input time")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("records must be an array of objects")
    matched, missing, excluded = [], [], 0
    for index, row in enumerate(records):
        state, absent = _evaluate(row, filters)
        if state is True:
            matched.append(deepcopy(row))
        elif state is False:
            excluded += 1
        else:
            missing.append({"row_index": index, "fields": absent})
    if sort_by:
        present = [row for row in matched if _number(row.get(sort_by)) is not None]
        absent = [row for row in matched if _number(row.get(sort_by)) is None]
        present.sort(key=lambda row: _number(row[sort_by]), reverse=descending)
        matched = present + absent
    items = matched if limit is None else matched[:limit]
    return result_ok(
        source="screen_records",
        universe=universe,
        as_of=as_of,
        filters=deepcopy(filters),
        sort_by=sort_by,
        descending=descending,
        input_count=len(records),
        matched_count=len(matched),
        returned_count=len(items),
        excluded_count=excluded,
        missing_count=len(missing),
        missing_rows=missing,
        items=items,
        coverage="provided_records_only",
        note="Counts describe input rows; ordering is not an investment ranking.",
    )


def _dependencies(names):
    needed = set(names)
    for name in list(needed):
        needed.update(_FIELDS[name].get("dependencies", []))
    return needed


def _universe(market, codes, index_code):
    from scutio_data import breadth, universe

    if codes is None and index_code is None:
        return universe.stock_universe(market)
    upstream = None
    if index_code is not None:
        upstream = breadth.index_constituents(index_code, include_weights=False)
        if not upstream.get("ok"):
            return upstream
        codes = [row.get("symbol") or row.get("code") for row in upstream["items"]]
    elif isinstance(codes, (str, bytes)) or not isinstance(codes, (list, tuple)):
        raise ValueError("codes must be a list of stock identifiers")
    rows = {}
    exchange_names = {"上海证券交易所": "sh", "深圳证券交易所": "sz", "北京证券交易所": "bj"}
    inputs = upstream["items"] if upstream else [{"code": code} for code in codes]
    for raw in inputs:
        code = raw.get("symbol") or raw.get("code")
        _, exchange, pure = require_a_share(code, "screen_market")
        symbol = exchange + pure
        stated = raw.get("exchange")
        if stated and exchange_names.get(stated, stated) != exchange:
            raise ValueError("index constituent exchange mismatch")
        item = {
            **raw,
            "symbol": symbol,
            "code": pure,
            "exchange": exchange,
            "asset_type": "stock",
            "as_of": raw.get("date") or (upstream or {}).get("as_of"),
        }
        if symbol in rows and rows[symbol] != item:
            raise ValueError("conflicting duplicate index constituent")
        rows[symbol] = item
    return result_ok(
        source=upstream.get("source") if upstream else "requested_symbols",
        items=list(rows.values()),
        universe_type="index_constituents" if upstream else "requested_symbols",
        index_code=index_code,
        as_of=datetime.now(timezone.utc).isoformat(),
        is_complete=None if upstream else True,
        partial=bool(upstream and upstream.get("partial")),
        coverage={
            "input_count": len(codes),
            "unique_symbols": len(rows),
            "upstream": {key: value for key, value in (upstream or {}).items() if key != "items"},
        },
    )


def _quote_time(row):
    raw = row.get("data_as_of") or row.get("time")
    if not raw:
        return None
    try:
        text = str(raw)
        if len(text) == 14 and text.isdigit():
            parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return parsed.astimezone(ZoneInfo("Asia/Shanghai"))
    except ValueError:
        return None


def _quote_value(row, field, today, max_age):
    if row.get("currency") != "CNY":
        return None, "currency_unknown_or_mismatch"
    stamp = _quote_time(row)
    if stamp is None:
        return None, "quote_time_unknown"
    age = (today - stamp.date()).days
    if age < 0:
        return None, "future_quote"
    if age > max_age:
        return None, "stale_quote"
    value = _number(row.get(field))
    if value is None or field in ("price", "last_close", "mcap_yi", "float_mcap_yi") and value <= 0:
        return None, "missing_or_invalid"
    if field in ("pe_ttm", "pb") and value == 0:
        return None, "missing_or_invalid"
    if field in ("volume", "amount", "turnover_pct", "vol_ratio", "amplitude_pct") and value < 0:
        return None, "missing_or_invalid"
    return value, None


def _quote_batch(rows, fields, today, max_age):
    from scutio_data import market

    codes = [row["symbol"] for row in rows]
    verified = [
        row
        for row in rows
        if row.get("listing_status") == "listed"
        and str(row.get("source") or "").startswith("akshare:stock_info_")
    ]
    primary = market.security_quote(codes, **({"identity_records": verified} if verified else {}))
    quotes = primary.get("quotes") or {}
    refill = [
        row
        for row in rows
        if (quotes.get(row["symbol"]) or {}).get("source") == "hithink"
        and any(_quote_value(quotes[row["symbol"]], field, today, max_age)[1] for field in fields)
    ]
    supplement = (
        market.security_quote([row["symbol"] for row in refill], sources=("tencent", "sina"))
        if refill
        else {}
    )
    values = {}
    for row in rows:
        symbol = row["symbol"]
        result = {"symbol": symbol, "field_sources": {}, "field_status": {}}
        for field in fields:
            reason = "not_returned"
            for candidate in (quotes.get(symbol), (supplement.get("quotes") or {}).get(symbol)):
                if not isinstance(candidate, dict):
                    continue
                try:
                    validate_identity(candidate, symbol)
                except ValueError:
                    reason = "identity_mismatch"
                    continue
                value, reason = _quote_value(candidate, field, today, max_age)
                if reason is None:
                    result[field] = value
                    result["field_sources"][field] = {
                        "source": candidate.get("source"),
                        "data_as_of": _quote_time(candidate).isoformat(),
                        "retrieved_at": candidate.get("retrieved_at"),
                        "unit": _FIELDS[field]["unit"],
                        "currency": "CNY",
                    }
                    break
            else:
                result[field] = None
                result["field_status"][field] = reason
        values[symbol] = result
    return result_ok(
        source="screening_quotes",
        records=values,
        errors={
            "primary": primary.get("errors") or primary.get("error"),
            "supplement": supplement.get("errors") or supplement.get("error"),
        },
    )


def _progress(callback, phase):
    if callback is None:
        return None
    return lambda event: callback({**event, "phase": phase})


def _put(row, field, value, source, reason=None):
    row[field] = value
    if value is None:
        row["field_status"][field] = reason or "missing_or_invalid"
    else:
        row["field_sources"][field] = source
        row["field_status"].pop(field, None)


def _financial_rows(envelope):
    # Duplicates must have been resolved by the domain adapter, never last-row-wins here.
    rows, conflicts = {}, set()
    if not envelope.get("ok"):
        return rows
    for row in envelope.get("items") or []:
        symbol = row.get("symbol")
        try:
            require_a_share(symbol, "screen_market")
            validate_identity(row, symbol)
        except ValueError:
            conflicts.add(symbol)
            continue
        if symbol in rows and row != rows[symbol]:
            conflicts.add(symbol)
        rows[symbol] = row
    return {symbol: row for symbol, row in rows.items() if symbol not in conflicts}


def _financial_issue(raw, expected, today):
    if raw.get("currency") != "CNY" or raw.get("report_date") != expected:
        return "missing_or_incompatible_report"
    status = (raw.get("field_status") or {}).get("disclosed_at")
    if status:
        return "source_update_" + str(status)
    try:
        disclosed = date.fromisoformat(str(raw.get("disclosed_at") or "")[:10])
    except ValueError:
        return "source_update_missing_or_invalid"
    if disclosed > today:
        return "future_source_update"
    if disclosed.isoformat() < expected:
        return "source_update_before_report_date"
    return None


def _financial_metadata(raw, envelope, field):
    spec = (envelope.get("field_catalog") or {}).get(field) or {}
    retrieved = envelope.get("snapshot_retrieved_at", envelope.get("retrieved_at"))
    if isinstance(retrieved, (int, float)):
        retrieved = datetime.fromtimestamp(retrieved, timezone.utc).isoformat()
    return {
        "source": raw.get("source") or envelope.get("source"),
        "report_date": raw.get("report_date"),
        "disclosed_at": raw.get("disclosed_at"),
        "disclosed_at_basis": raw.get("disclosed_at_basis"),
        "time_basis": raw.get("time_basis") or envelope.get("time_basis"),
        "basis": spec.get("basis"),
        "source_field": spec.get("source_field"),
        "native_field": spec.get("native_field"),
        "unit": _FIELDS[field]["unit"],
        "retrieved_at": retrieved,
    }


def screen_market(
    filters,
    *,
    market="a",
    codes=None,
    index_code=None,
    report_date=None,
    fields=None,
    sort_by=None,
    descending=True,
    limit=50,
    max_workers=4,
    quote_max_age_days=7,
    on_progress=None,
    cancel_event=None,
):
    """Build current A-share features and screen them; no global default time limit.

    report_date is required for financial fields. Quotes must have a source time
    within quote_max_age_days; retrieval time is not a quote time. Progress is
    metadata only and never marks a partial batch as the completed screen.
    """
    try:
        _validate(filters, sort_by, descending, limit)
        if market != "a":
            raise ValueError("screen_market currently supports A shares only")
        if codes is not None and index_code is not None:
            raise ValueError("choose codes or index_code, not both")
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be 1..16; source quotas still apply")
        if type(quote_max_age_days) is not int or quote_max_age_days < 0:
            raise ValueError("quote_max_age_days must be a nonnegative integer")
        if fields is not None and (
            not isinstance(fields, list) or any(not isinstance(f, str) for f in fields)
        ):
            raise ValueError("fields must be a list of catalog field names")
        if on_progress is not None and not callable(on_progress):
            raise ValueError("on_progress must be callable")
        wanted = set(filters) | set(fields or []) | ({sort_by} if sort_by else set())
        if wanted - _FIELDS.keys():
            raise ValueError("unsupported features; consult feature_catalog before requesting data")
        if sort_by and _FIELDS[sort_by]["unit"] in ("text", "YYYY-MM-DD"):
            raise ValueError("sort_by must name a numeric catalog feature")
        needed = _dependencies(wanted)
        financial = {field for field in needed if _FIELDS[field]["group"] == "financial"}
        report_day = None
        if financial:
            report_day = date.fromisoformat(str(report_date))
            if (report_day.month, report_day.day) not in ((3, 31), (6, 30), (9, 30), (12, 31)):
                raise ValueError("report_date must be an explicit quarter end (YTD/annual)")
            first_year = (
                2011 if any(_FIELDS[field].get("prior_year") for field in financial) else 2010
            )
            if report_day < date(first_year, 3, 31):
                raise ValueError("report_date precedes the financial source's available periods")
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        if report_day and report_day > now.date():
            raise ValueError("report_date cannot be in the future")
        with cancellation_scope(cancel_event):
            check_cancelled()
            return _screen_market(
                filters,
                market,
                codes,
                index_code,
                report_day,
                wanted,
                needed,
                sort_by,
                descending,
                limit,
                max_workers,
                quote_max_age_days,
                on_progress,
                cancel_event,
                now,
            )
    except RequestCancelled as exc:
        return result_err(
            exc.reason,
            source="screen_market",
            error_code="cancelled",
            run_state="interrupted",
            partial=True,
            items=[],
        )
    except (TypeError, ValueError) as exc:
        return result_err(
            str(exc), source="screen_market", error_code="invalid_arguments", items=[]
        )


def _screen_market(
    filters,
    market,
    codes,
    index_code,
    report_day,
    wanted,
    needed,
    sort_by,
    descending,
    limit,
    max_workers,
    quote_max_age_days,
    on_progress,
    cancel_event,
    now,
):
    from scutio_data import fundamentals

    universe = _universe(market, codes, index_code)
    if not universe.get("ok"):
        return result_err(
            "security universe unavailable",
            source="screen_market",
            items=[],
            partial=True,
            universe=universe,
            error_code="universe_unavailable",
        )
    records = []
    seen = set()
    for raw in universe["items"]:
        symbol = raw["symbol"]
        validate_identity(raw, symbol)
        if symbol in seen:
            raise ValueError("duplicate universe identity")
        seen.add(symbol)
        row = {**raw, "field_sources": {}, "field_status": {}}
        for field in needed:
            if _FIELDS[field]["group"] != "universe":
                continue
            value = raw.get(field) or None
            if field == "listing_age_days":
                try:
                    value = (now.date() - date.fromisoformat(raw.get("listed_date") or "")).days
                    if value < 0:
                        value = None
                except ValueError:
                    value = None
            _put(
                row,
                field,
                value,
                {
                    "source": raw.get("source") or universe.get("source"),
                    "data_as_of": raw.get("as_of") or universe.get("as_of"),
                    "unit": _FIELDS[field]["unit"],
                },
            )
        records.append(row)
    stages = {}
    quote_fields = {
        field
        for field in needed
        if _FIELDS[field]["group"] == "quote" and not _FIELDS[field].get("dependencies")
    }
    pending = [row for row in records if _evaluate(row, filters)[0] is not False]
    if quote_fields and pending:
        calls = {
            str(start // 100): partial(
                _quote_batch,
                pending[start : start + 100],
                quote_fields,
                now.date(),
                quote_max_age_days,
            )
            for start in range(0, len(pending), 100)
        }
        quoted = fetch_many(
            calls,
            max_workers=max_workers,
            on_progress=_progress(on_progress, "quotes"),
            cancel_event=cancel_event,
        )
        stages["quotes"] = quoted
        fetched = {}
        for task in quoted["results"].values():
            fetched.update((task.get("result") or {}).get("records") or {})
        for row in pending:
            enriched = fetched.get(row["symbol"], {})
            for field in quote_fields:
                _put(
                    row,
                    field,
                    enriched.get(field),
                    (enriched.get("field_sources") or {}).get(field),
                    (enriched.get("field_status") or {}).get(
                        field, "request_failed_or_not_started"
                    ),
                )
            if "earnings_yield_pct" in needed:
                pe = row.get("pe_ttm")
                value = _number(100 / pe) if pe else None
                _put(
                    row,
                    "earnings_yield_pct",
                    value,
                    {
                        "formula": _FIELDS["earnings_yield_pct"]["formula"],
                        "inputs": {"pe_ttm": row["field_sources"].get("pe_ttm")},
                    },
                    row["field_status"].get("pe_ttm"),
                )
    pending = [row for row in records if _evaluate(row, filters)[0] is not False]
    financial = {field for field in needed if _FIELDS[field]["group"] == "financial"}
    if financial and pending:
        requested = [row["symbol"] for row in pending]
        calls = {
            "current": partial(
                fundamentals.financial_snapshot, report_day.isoformat(), codes=requested
            )
        }
        if any(_FIELDS[field].get("prior_year") for field in financial):
            previous = report_day.replace(year=report_day.year - 1)
            calls["prior"] = partial(
                fundamentals.financial_snapshot, previous.isoformat(), codes=requested
            )
        fetched = fetch_many(
            calls,
            max_workers=max_workers,
            on_progress=_progress(on_progress, "financials"),
            cancel_event=cancel_event,
        )
        stages["financials"] = fetched
        envs = {key: task.get("result") or {} for key, task in fetched["results"].items()}
        current, prior = _financial_rows(envs["current"]), _financial_rows(envs.get("prior", {}))
        for row in pending:
            raw = current.get(row["symbol"], {})
            for field in financial:
                if _FIELDS[field].get("dependencies"):
                    continue
                value = (
                    raw.get(field) if _FIELDS[field]["unit"] == "text" else _number(raw.get(field))
                )
                status = _financial_issue(raw, report_day.isoformat(), now.date()) or (
                    raw.get("field_status") or {}
                ).get(field)
                if status:
                    value = None
                _put(
                    row,
                    field,
                    value,
                    _financial_metadata(raw, envs["current"], field),
                    status,
                )
            for field in financial:
                deps = _FIELDS[field].get("dependencies")
                if not deps:
                    continue
                numerator = row.get(deps[0])
                old = prior.get(row["symbol"], {})
                denominator = (
                    _number(old.get(deps[0]))
                    if _FIELDS[field].get("prior_year")
                    else row.get(deps[1])
                )
                reason = None
                if _FIELDS[field].get("prior_year") and (
                    _financial_issue(old, previous.isoformat(), now.date())
                    or (old.get("field_status") or {}).get(deps[0])
                ):
                    denominator = None
                if numerator is None or denominator is None:
                    value, reason = None, "dependency_missing"
                elif denominator <= 0:
                    value, reason = None, "nonpositive_base"
                else:
                    value = _number(
                        (numerator / denominator - int(bool(_FIELDS[field].get("prior_year"))))
                        * 100
                    )
                _put(
                    row,
                    field,
                    value,
                    {
                        "formula": _FIELDS[field]["formula"],
                        "inputs": {dep: row["field_sources"].get(dep) for dep in deps},
                        "prior_report_date": old.get("report_date"),
                        "prior_value": denominator if _FIELDS[field].get("prior_year") else None,
                        "prior_source": _financial_metadata(old, envs.get("prior", {}), deps[0])
                        if _FIELDS[field].get("prior_year")
                        else None,
                    },
                    reason,
                )
    result = screen_records(
        records,
        filters,
        universe=universe.get("universe_type") or "stock_universe",
        as_of=now.isoformat(),
        sort_by=sort_by,
        descending=descending,
        limit=limit,
    )
    missing = [
        {
            "symbol": records[item["row_index"]]["symbol"],
            "fields": item["fields"],
            "reasons": {
                field: records[item["row_index"]]["field_status"].get(field, "missing")
                for field in item["fields"]
            },
        }
        for item in result["missing_rows"]
    ]
    interrupted = any(stage["batch_state"] == "interrupted" for stage in stages.values())
    complete = universe.get("is_complete")
    evaluable = len(records) - result["missing_count"]
    candidates = [row for row in records if _evaluate(row, filters)[0] is not False]
    field_coverage = {}
    for field in sorted(wanted):
        absent = [row for row in candidates if row.get(field) is None]
        reasons = {}
        for row in absent:
            reason = row["field_status"].get(field, "missing")
            reasons[reason] = reasons.get(reason, 0) + 1
        field_coverage[field] = {
            "scope": "not_excluded_by_known_conditions",
            "requested": len(candidates),
            "available": len(candidates) - len(absent),
            "missing": len(absent),
            "reasons": reasons,
        }
    fields_complete = not any(item["missing"] for item in field_coverage.values())
    result.update(
        source="screen_market",
        universe={key: value for key, value in universe.items() if key != "items"},
        missing_rows=missing,
        run_state="interrupted" if interrupted else "finished",
        partial=complete is not True or bool(missing) or interrupted or not fields_complete,
        fields=sorted(wanted),
        feature_definitions={field: deepcopy(_FIELDS[field]) for field in sorted(needed)},
        report_date=report_day.isoformat() if report_day else None,
        mode="current_snapshot",
        point_in_time_verified=False,
        quote_max_age_days=quote_max_age_days,
        coverage={
            "universe_complete": complete,
            "unique_symbols": len(records),
            "evaluable": evaluable,
            "unknown": len(missing),
            "conditions_evaluated": not missing,
            "fields": field_coverage,
            "requested_fields_complete": fields_complete,
            "sort_complete": not sort_by or field_coverage[sort_by]["missing"] == 0,
            "screen_complete": False
            if missing or interrupted or complete is False or not fields_complete
            else complete,
        },
        execution={
            phase: {
                "batch_state": stage["batch_state"],
                "timing": stage["timing"],
                "tasks": {
                    key: {
                        "state": task["state"],
                        "error": (task.get("result") or {}).get("error"),
                        "errors": (task.get("result") or {}).get("errors"),
                    }
                    for key, task in stage["results"].items()
                },
            }
            for phase, stage in stages.items()
        },
        note="Counts describe unique securities in the returned universe; missing data is not a negative match.",
    )
    return result
