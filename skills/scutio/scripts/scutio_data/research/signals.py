"""Fixed-window forecast/price comparisons on supplied evidence; no network or backtest."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from scutio_data._runtime.parsing import finite_number
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import require_a_share, validate_identity
from scutio_data.research.consensus import _conflicting_forecast_fields, _forecast_observations


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("dates must be YYYY-MM-DD")
    return date.fromisoformat(value)


def _available(row, endpoint):
    """Date-only material is eligible after its date, never at that day's close."""
    display = _day(row["date"])
    cutoff = datetime.combine(endpoint, time(15), ZoneInfo("Asia/Shanghai"))
    explicit = []
    publication = display
    for key in ("published_at", "available_at"):
        value = row.get(key)
        if value in (None, ""):
            continue
        if len(str(value)) == 10:
            parsed = _day(value)
            explicit.append((parsed, parsed < endpoint))
            if key == "published_at":
                publication = parsed
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("publication timestamps require a timezone")
            explicit.append((parsed.astimezone(cutoff.tzinfo).date(), parsed <= cutoff))
            if key == "published_at":
                publication = parsed.astimezone(cutoff.tzinfo).date()
    if explicit:
        # A later platform listing cannot establish earlier public availability.
        return display <= endpoint and all(allowed for _, allowed in explicit), publication
    return display < endpoint, display


def _endpoint(rows, endpoint, max_age_days):
    candidates = []
    for row in rows:
        allowed, effective = _available(row, endpoint)
        if allowed:
            candidates.append((effective, row))
    if not candidates:
        return None, ["forecast_missing"]
    latest = max(day for day, _ in candidates)
    available_by_day = {}
    for _, row in candidates:
        available_by_day.setdefault(row["date"], []).append(row)
    conflicts = {
        day: bool(_conflicting_forecast_fields(rows)) for day, rows in available_by_day.items()
    }
    group = [
        {
            **row,
            "conflict": conflicts[row["date"]],
            "conflict_reason": "same_day_forecast_conflict" if conflicts[row["date"]] else None,
        }
        for day, row in candidates
        if day == latest
    ]
    # Preserve the latest conflicted group. Never retreat to a favorable older EPS.
    snapshot = {
        "observations": group,
        "date": latest.isoformat(),
        "age_days": (endpoint - latest).days,
        "conflict_scope": "endpoint_available_materials",
    }
    reasons = []
    if any(row.get("conflict") for row in group) or len({row["eps"] for row in group}) != 1:
        reasons.append("forecast_conflict")
    if snapshot["age_days"] > max_age_days:
        reasons.append("forecast_stale")
    if not reasons:
        snapshot["eps"] = group[0]["eps"]
    return snapshot, reasons


def _prices(code, bars, start, end):
    if not isinstance(bars, dict):
        raise ValueError("bars must be an identified security_bars envelope")
    validate_identity(bars, code)
    rows = bars.get("bars", [])
    if not isinstance(rows, list):
        raise ValueError("bars must contain a list")
    for row in rows:
        validate_identity(row, code, required=False)
        for key in ("currency", "adjust"):
            if row.get(key) is not None and row[key] != bars.get(key):
                raise ValueError("bar row %s conflicts with envelope" % key)
    issues = []
    if not bars.get("ok"):
        issues.append("bars_failed: " + str(bars.get("error") or "unknown"))
    if bars.get("adjust") != "none":
        issues.append("prices_must_be_unadjusted")
    if bars.get("frequency") not in ("D", "1D"):
        issues.append("prices_must_be_daily")
    if not bars.get("currency"):
        issues.append("price_currency_unknown")
    result = {}
    for label, endpoint in (("start", start), ("end", end)):
        matches = [
            row for row in rows if str(row.get("datetime") or "")[:10] == endpoint.isoformat()
        ]
        values = [finite_number(row.get("close")) for row in matches]
        if not values or any(value is None or value <= 0 for value in values):
            issues.append(label + "_price_missing_or_invalid")
        elif len(set(values)) != 1:
            issues.append(label + "_price_conflict")
        else:
            result[label] = {
                "date": endpoint.isoformat(),
                "price": values[0],
                "currency": bars.get("currency"),
                "source": bars.get("source"),
                "retrieved_at": bars.get("retrieved_at"),
                "adjust": bars.get("adjust"),
            }
    return result, issues


def _share_basis(code, evidence, start, end):
    if evidence is None:
        return ["share_basis_unverified"], "unknown"
    if not isinstance(evidence, dict):
        raise ValueError("share_basis must be a dated evidence object")
    validate_identity(evidence, code)
    if (
        evidence.get("start_date") != start.isoformat()
        or evidence.get("end_date") != end.isoformat()
    ):
        raise ValueError("share_basis evidence window mismatch")
    status = evidence.get("status", "unknown")
    if status not in ("unchanged", "changed", "unknown"):
        raise ValueError("share_basis status must be unchanged/changed/unknown")
    cash = evidence.get("cash_dividends", "unknown")
    if cash not in ("none", "present", "unknown"):
        raise ValueError("cash_dividends must be none/present/unknown")
    references = evidence.get("evidence")
    verified = isinstance(references, list) and any(
        isinstance(item, str) and item.strip() for item in references
    )
    if not verified:
        return ["share_basis_unverified"], "unknown"
    return ([] if status == "unchanged" else ["share_basis_" + status]), cash


def _comparable(start, end, currency):
    reasons = []
    for field in ("currency", "eps_basis", "eps_definition"):
        values = {row.get(field) for snapshot in (start, end) for row in snapshot["observations"]}
        if None in values or "" in values:
            reasons.append(field + "_unknown")
        elif len(values) != 1:
            reasons.append(field + "_mismatch")
        elif field == "currency" and currency and values != {currency}:
            reasons.append("price_eps_currency_mismatch")
    return reasons


def _pair(
    organization,
    rows,
    *,
    start,
    end,
    max_age_days,
    eps_floor,
    prices,
    price_issues,
    basis_issues,
    currency,
    skipped,
):
    first, first_issues = _endpoint(rows, start, max_age_days)
    last, last_issues = _endpoint(rows, end, max_age_days)
    reasons = ["start_" + reason for reason in first_issues] + [
        "end_" + reason for reason in last_issues
    ]
    reasons += basis_issues
    if any(
        _blocks_endpoint(row, point, snapshot)
        for row in skipped
        for point, snapshot in ((start, first), (end, last))
    ):
        reasons.append("unusable_forecast_records")
    if first and last and not first_issues and not last_issues:
        reasons += _comparable(first, last, currency)
    result = {
        "organization": organization,
        "start_forecast": first,
        "end_forecast": last,
        "start_price": prices.get("start"),
        "end_price": prices.get("end"),
        "coverage_status": "paired"
        if first and last and not first_issues and not last_issues
        else "new_coverage"
        if last and not last_issues
        else "old_only"
        if first and not first_issues
        else "unavailable",
        "eps_comparable": not reasons,
        "eps_change": None,
        "eps_change_pct": None,
        "direction": None,
        "unchanged_report": bool(first and last and first["observations"] == last["observations"]),
        "price_change_pct": None,
        "pe0": None,
        "pe1": None,
        "pe_change_pct": None,
        "metrics_computable": False,
        "matched": None,
        "reasons": reasons,
    }
    if result["eps_comparable"]:
        e0, e1 = first["eps"], last["eps"]
        result["eps_change"] = finite_number(e1 - e0)
        result["direction"] = "up" if e1 > e0 else "down" if e1 < e0 else "flat"
        if e0 <= 0 or e1 <= 0:
            reasons.append("nonpositive_eps")
        elif min(e0, e1) <= eps_floor:
            reasons.append("near_zero_eps")
        else:
            result["eps_change_pct"] = finite_number((e1 / e0 - 1) * 100)
            if result["eps_change_pct"] is None:
                reasons.append("nonfinite_calculation")
    reasons += price_issues
    if not reasons:
        p0, p1 = prices["start"]["price"], prices["end"]["price"]
        pe0, pe1 = finite_number(p0 / first["eps"]), finite_number(p1 / last["eps"])
        if not pe0 or not pe1:
            reasons.append("nonfinite_calculation")
        else:
            calculated = {
                "price_change_pct": finite_number((p1 / p0 - 1) * 100),
                "pe0": pe0,
                "pe1": pe1,
                "pe_change_pct": finite_number((pe1 / pe0 - 1) * 100),
            }
            if any(value is None for value in calculated.values()):
                reasons.append("nonfinite_calculation")
            else:
                result.update(
                    **calculated,
                    metrics_computable=True,
                    matched=result["eps_change_pct"] > 0 and pe1 < pe0,
                )
    return result


def _relevant_skip(row, fiscal_year, end):
    years = row.get("forecast_years")
    if (
        isinstance(years, list)
        and years
        and all(str(year).isdigit() for year in years)
        and str(fiscal_year) not in {str(year) for year in years}
    ):
        return False
    if row.get("date"):
        try:
            if not _available(row, end)[0]:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _blocks_endpoint(row, endpoint, snapshot):
    if not row.get("date"):
        return True
    try:
        allowed, published = _available(row, endpoint)
    except (TypeError, ValueError):
        return True
    return allowed and (snapshot is None or published >= _day(snapshot["date"]))


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    low, high = ordered[middle - 1 : middle + 1]
    # Avoid overflowing the sum of two finite values or their signed difference.
    return low / 2 + high / 2 if low <= 0 <= high else low + (high - low) / 2


def forecast_price_changes(
    code,
    reports,
    bars,
    *,
    start_date,
    end_date,
    fiscal_year,
    max_age_days,
    eps_floor,
    share_basis=None,
    limit=None,
):
    """Compare one company's same-year broker forecasts and raw endpoint closes.

    Inputs are supplied evidence. Date-only forecasts are eligible only after their
    stated date. This retrospective view never attests historical availability or
    return predictability. Unknown share/earnings bases block automatic matches.
    """
    source = "forecast_price_changes"
    try:
        _, prefix, pure = require_a_share(code, source)
        symbol = prefix + pure
        start, end = _day(start_date), _day(end_date)
        if start >= end:
            raise ValueError("start_date must precede end_date")
        if (
            isinstance(fiscal_year, bool)
            or not isinstance(fiscal_year, int)
            or not 1900 <= fiscal_year <= 2999
        ):
            raise ValueError("fiscal_year must be an absolute integer year")
        if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 0:
            raise ValueError("max_age_days must be a nonnegative integer")
        floor = finite_number(eps_floor)
        if floor is None or floor < 0:
            raise ValueError("eps_floor must be finite and nonnegative")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError("limit must be a positive integer or None")
        forecasts = _forecast_observations(code, reports)
        if not forecasts["ok"]:
            return result_list_err(
                forecasts["error"],
                source=source,
                symbol=symbol,
                input_forecasts=forecasts,
                coverage={
                    "companies": {
                        "requested": 1,
                        "fetched": 0,
                        "computable": 0,
                        "matched": 0,
                        "missing": 1,
                        "displayed": 0,
                    }
                },
            )
        prices, price_issues = _prices(code, bars, start, end)
        basis_issues, dividends = _share_basis(code, share_basis, start, end)
        groups = {}
        temporal_errors = []
        for observation in forecasts["items"]:
            if observation["fiscal_year"] != fiscal_year:
                continue
            group = groups.setdefault(observation["organization"], [])
            try:
                _available(observation, end)
            except (TypeError, ValueError):
                temporal_errors.append(
                    {
                        "organization": observation["organization"],
                        "reason": "invalid_availability",
                        "info_code": observation.get("info_code"),
                    }
                )
            else:
                group.append(observation)
        skipped = [
            row
            for row in (forecasts.get("skipped_reports") or [])
            if _relevant_skip(row, fiscal_year, end)
        ] + temporal_errors
        for item in skipped:
            if isinstance(item.get("organization"), str) and item["organization"].strip():
                groups.setdefault(item["organization"], [])
        items = []
        for organization, rows in sorted(groups.items()):
            item = _pair(
                organization,
                rows,
                start=start,
                end=end,
                max_age_days=max_age_days,
                eps_floor=floor,
                prices=prices,
                price_issues=price_issues,
                basis_issues=basis_issues,
                currency=bars.get("currency"),
                skipped=[row for row in skipped if row.get("organization") == organization],
            )
            item.update(symbol=symbol, fiscal_year=fiscal_year, cash_dividends=dividends)
            items.append(item)
        computable = [item for item in items if item["metrics_computable"]]
        paired = [item for item in items if item["eps_comparable"]]
        hits = sum(item["matched"] is True for item in items)
        directions = {
            direction: sum(item["direction"] == direction for item in paired)
            for direction in ("up", "down", "flat")
        }
        metrics = {}
        for key in ("eps_change_pct", "pe_change_pct"):
            values = [item[key] for item in items if item[key] is not None]
            metrics[key] = {
                "values": values,
                "count": len(values),
                "median": _median(values),
            }
        displayed = items if limit is None else items[:limit]
        incomplete = bool(
            forecasts.get("partial")
            or bars.get("partial")
            or skipped
            or price_issues
            or basis_issues
            or any(item["reasons"] for item in items)
            or not items
            or dividends == "unknown"
        )
        return result_list(
            displayed,
            source=source,
            code=pure,
            symbol=symbol,
            fiscal_year=fiscal_year,
            start_date=start_date,
            end_date=end_date,
            max_age_days=max_age_days,
            eps_floor=floor,
            mode="retrospective_current_materials",
            point_in_time_verified=False,
            date_only_policy="eligible_after_stated_date",
            share_basis=share_basis,
            cash_dividends=dividends,
            partial=incomplete or len(displayed) < len(items),
            matched=bool(hits) if computable else None,
            summary={
                "organizations": len(items),
                "paired": len(paired),
                "computable": len(computable),
                "matched": hits,
                **directions,
                "new_coverage": sum(item["coverage_status"] == "new_coverage" for item in items),
                "old_only": sum(item["coverage_status"] == "old_only" for item in items),
                "not_updated": sum(item["unchanged_report"] for item in items),
                "disagreement": bool(directions["up"] and directions["down"]),
                "metrics": metrics,
            },
            coverage={
                "companies": {
                    "requested": 1,
                    "fetched": int(bool(bars.get("ok"))),
                    "computable": int(bool(computable)),
                    "matched": int(bool(hits)),
                    "missing": int(incomplete),
                    "displayed": int(bool(displayed)),
                },
                "organizations": {
                    "total": len(items),
                    "returned": len(displayed),
                    "truncated": len(displayed) < len(items),
                },
                "forecasts": forecasts.get("coverage"),
                "bars": bars.get("coverage"),
            },
            skipped_reports=skipped,
            conflicts=forecasts.get("conflicts", []),
            input_provenance={
                "forecasts": {
                    key: forecasts.get(key)
                    for key in ("input_source", "input_retrieved_at", "errors", "partial")
                },
                "bars": {
                    key: bars.get(key) for key in ("source", "retrieved_at", "errors", "partial")
                },
            },
            warning="按当前可得材料回溯；仅为研究线索，不证明历史可交易、市场遗漏或收益预测能力。"
            + (
                " 公司行动未完全核实。"
                if dividends == "unknown"
                else " 价格变化包含现金分红影响。"
                if dividends == "present"
                else ""
            ),
        )
    except (TypeError, ValueError, KeyError) as exc:
        return result_list_err(str(exc), source=source)
