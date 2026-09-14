"""Official Financial API protocol over the shared source execution boundary."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import requests

from scutio_data._runtime import config
from scutio_data._runtime.execution import CacheSpec, SourceFailure, execute, network_identity
from scutio_data._runtime.http import Session, retry_after
from scutio_data._runtime.symbols import is_a_share as eligible
from scutio_data._runtime.symbols import split_code
from scutio_data._runtime.timeouts import source, source_budget

BASE = "https://fuyao.aicubes.cn"
CACHE_VERSION = 4


_FINANCIAL_FIELDS = {
    key: value.split()
    for key, value in {
        "lrb": "basic_eps operating_income operating_costs operating_expenses operating_profit profit_total net_profit parent_holder_net_profit income_tax_expense interest_expenses manage_fee sales_fee research_and_development_expenses",
        "fzb": "total_current_assets non_current_nets_total assets_total total_debt holder_equity_total cash accounts_receivable",
        "llb": "act_cash_flow_net invest_cash_flow_net financing_cash_flow_net cash_equivalents_net_addition pay_dividends_profits_interest_cash pay_fixed_assets_etc_cash",
    }.items()
}


class SourceError(RuntimeError):
    """Credential-free source errors exposed to the domain adapters."""


def preferred(code):
    return config.enabled() and eligible(code)


def namespace(key):
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _valid_value(value, path, params):
    if not isinstance(value, dict) or value.get("source") != "hithink":
        return False
    data = value.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
        return False
    rows = data["item"]
    if any(not isinstance(row, dict) for row in rows):
        return False
    if path.startswith("/api/a-share/financials/"):
        expected_period = "annual" if params.get("period") == "annual" else "quarterly"
        if any(
            row.get("thscode") != params.get("thscode")
            or row.get("currency") != "CNY"
            or row.get("period") != expected_period
            for row in rows
        ):
            return False
    elif path.endswith(("/historical", "/adjustment-factors")):
        if data.get("thscode") != params.get("thscode"):
            return False
        if path.endswith("/historical") and any(
            data.get(key) != params.get(key) for key in ("adjust", "interval")
        ):
            return False
    elif path.endswith("/snapshot"):
        expected = set(str(params.get("thscodes", "")).split(","))
        if any(row.get("thscode") not in expected for row in rows):
            return False
    if not isinstance(value.get("retrieved_at"), str):
        return False
    # The response-level timestamp does not establish a business data time.
    if value.get("data_as_of") is not None:
        return False
    from scutio_data._providers.hithink import parse

    try:
        if path.startswith("/api/a-share/financials/"):
            report = {
                "income-statements": "lrb",
                "balance-sheets": "fzb",
                "cash-flow-statements": "llb",
            }.get(path.rsplit("/", 1)[-1])
            if report:
                for row in rows:
                    parse.date_ms(row.get("period_end_ms"))
                    values = [parse.number(row.get(field)) for field in _FINANCIAL_FIELDS[report]]
                    if all(value is None for value in values):
                        return False
        elif path.endswith("/prices/historical"):
            parse.bars(rows, params["thscode"], {})
        elif path.endswith("/adjustment-factors"):
            parse.dividends(rows, params["thscode"])
        elif path.endswith("/valuations/snapshot"):
            for row in rows:
                values = [
                    parse.number(row.get(field))
                    for field in ("pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm")
                ]
                if all(value is None for value in values):
                    return False
    except (ValueError, TypeError, OverflowError, OSError):
        return False
    return True


def _transport(path, params, key):
    with Session() as session:
        session.headers.update({"X-api-key": key, "Accept": "application/json"})
        try:
            response = session.get(BASE + path, params=params, allow_redirects=False)
        except requests.RequestException as exc:
            code = (
                "proxy_error"
                if isinstance(exc, requests.exceptions.ProxyError)
                else "tls_error"
                if isinstance(exc, requests.exceptions.SSLError)
                else "upstream_timeout"
                if isinstance(exc, requests.exceptions.Timeout)
                else "upstream_connection_error"
                if isinstance(exc, requests.exceptions.ConnectionError)
                else "transient"
            )
            raise SourceFailure(code, "hithink: " + code) from None
        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        code = payload.get("code") if isinstance(payload, dict) else None
        if 200 <= status < 300 and type(code) is int and code == 0:
            data = payload.get("data")
            if not isinstance(data, dict):
                raise SourceFailure("invalid_response", "hithink: invalid response schema")
            timestamp = data.get("timestamp")
            try:
                if isinstance(timestamp, bool):
                    raise ValueError("invalid timestamp")
                provider_timestamp = (
                    datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()
                    if timestamp is not None
                    else None
                )
            except (TypeError, ValueError, OverflowError, OSError):
                raise SourceFailure(
                    "invalid_response", "hithink: invalid source timestamp"
                ) from None
            value = {
                "data": data,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "provider_timestamp": provider_timestamp,
                "data_as_of": None,
                "source": "hithink",
            }
            if not _valid_value(value, path, params):
                raise SourceFailure(
                    "invalid_response", "hithink: invalid response schema or identity"
                )
            return value
        reason = (
            "authentication"
            if status == 401 or code == 2001
            else "permission"
            if status == 403 or code == 2003
            else "rate_limited"
            if status == 429 or code == 4001
            else "data_not_ready"
            if code == 3002
            else "transient"
            if status >= 500 or isinstance(code, int) and code >= 5000
            else "request_rejected"
        )
        raise SourceFailure(
            reason,
            "hithink: " + reason,
            retry_after=retry_after(response.headers.get("Retry-After"))
            if reason == "rate_limited"
            else None,
            scope="provider" if reason in ("authentication", "rate_limited") else "endpoint",
        )


def request(path, params, *, ttl=0):
    if not config.enabled():
        raise SourceError("hithink: disabled or key not configured")
    key = config.credential()[0]
    with source_budget():
        try:
            return execute(
                "hithink",
                path,
                lambda: _transport(path, params, key),
                parameters=params,
                credential_scope=namespace(key),
                route=network_identity(),
                attempts=2,
                cache=CacheSpec(
                    ttl=ttl,
                    version=CACHE_VERSION,
                    validator=lambda value: _valid_value(value, path, params),
                )
                if ttl
                else None,
            )
        except SourceFailure as exc:
            raise SourceError(str(exc)) from None


def identity(code):
    if not eligible(code):
        raise SourceError("hithink: unsupported asset")
    prefix, pure = split_code(code)
    symbol = pure + "." + prefix.upper()
    response = request("/api/meta/tickers/search", {"q": symbol, "limit": 20}, ttl=21600)
    for row in response["data"]["item"]:
        if row.get("thscode") == symbol and row.get("asset_type") == "a-share":
            return row
    raise SourceError("hithink: exact A-share identity not found")


def provenance(response):
    return {
        key: response.get(key)
        for key in ("source", "retrieved_at", "data_as_of", "provider_timestamp")
    }


@source("query")
def quotes(codes):
    from scutio_data._providers.hithink import parse

    metas = {}
    failure = "hithink: no eligible identities"
    for code in codes:
        try:
            meta = identity(code)
            metas[meta["thscode"]] = meta
        except SourceError as exc:
            failure = str(exc)
            continue
    if not metas:
        raise SourceError(failure)
    result = {}
    keys = list(metas)
    for start in range(0, len(keys), 100):
        response = request(
            "/api/a-share/prices/snapshot", {"thscodes": ",".join(keys[start : start + 100])}, ttl=5
        )
        for row in response["data"]["item"]:
            meta = metas.get(row.get("thscode"))
            if meta:
                try:
                    mapped = parse.quote(row, meta, provenance(response))
                    result[mapped["symbol"]] = mapped
                except ValueError:
                    continue  # The facade fills only missing/invalid securities.
    return result


@source("history")
def bars(code, frequency="D", count=80, adjust="none", index=None):
    from datetime import timedelta

    from scutio_data._providers.hithink import parse

    if frequency not in ("D", "1D") or index:
        raise SourceError("hithink: only A-share daily bars supported")
    if adjust not in ("none", "qfq", "hfq"):
        raise ValueError("adjust must be none, qfq or hfq")
    meta = identity(code)
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Bounded to two windows, each shorter than the official 10-year ceiling.
    result = []
    for multiplier in (2, 4):
        start = end - timedelta(days=min(3640, max(30, count * multiplier)))
        response = request(
            "/api/a-share/prices/historical",
            {
                "thscode": meta["thscode"],
                "interval": "1d",
                "start": int(start.timestamp() * 1000),
                "end": int(end.timestamp() * 1000),
                "adjust": {"none": "none", "qfq": "forward", "hfq": "backward"}[adjust],
            },
            ttl=60,
        )
        data = response["data"]
        expected_adjust = {"none": "none", "qfq": "forward", "hfq": "backward"}[adjust]
        if (
            data.get("thscode") != meta["thscode"]
            or data.get("adjust") != expected_adjust
            or data.get("interval") != "1d"
        ):
            raise SourceError("hithink: bar identity/adjustment mismatch")
        result = parse.bars(data["item"], meta["thscode"], provenance(response))
        if len(result) >= count:
            break
    return result[-count:]


@source("query")
def valuation(code):
    from scutio_data._providers.hithink.parse import number

    meta = identity(code)
    response = request("/api/a-share/valuations/snapshot", {"thscodes": meta["thscode"]}, ttl=30)
    for row in response["data"]["item"]:
        if row.get("thscode") == meta["thscode"]:
            values = {
                key: number(row.get(key))
                for key in ("pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm")
            }
            if all(value is None for value in values.values()):
                break
            return {
                **values,
                "pb": values["pb_mrq"],
                "pb_basis": "MRQ",
                **provenance(response),
                "timestamp_basis": "provider response timestamp; metric update times unknown",
            }
    raise SourceError("hithink: valuation missing")


@source("history")
def dividends(code, count=20):
    from scutio_data._providers.hithink import parse

    meta = identity(code)
    response = request(
        "/api/a-share/corporate-actions/adjustment-factors", {"thscode": meta["thscode"]}, ttl=3600
    )
    if response["data"].get("thscode") != meta["thscode"]:
        raise SourceError("hithink: dividend identity mismatch")
    return {
        "ok": True,
        "error": None,
        "items": parse.dividends(response["data"]["item"], meta["thscode"])[:count],
        **provenance(response),
        "partial": True,
        "warning": "Record/payment dates and bonus/transfer split are unavailable",
        "units": {"bonus_rmb": "CNY per 10 shares", "dividend_per_share": "CNY per share"},
    }


@source("history")
def financial_summary(code, report_type, num, period):
    from scutio_data._providers.hithink.parse import date_ms, number

    endpoints = {"lrb": "income-statements", "fzb": "balance-sheets", "llb": "cash-flow-statements"}
    if not 1 <= num <= 20 or period not in ("annual", "all"):
        raise SourceError("hithink: summary supports 1..20 annual/all periods")
    meta = identity(code)
    response = request(
        "/api/a-share/financials/" + endpoints[report_type],
        {
            "thscode": meta["thscode"],
            "limit": num,
            "period": "annual" if period == "annual" else "quarterly",
        },
        ttl=3600,
    )
    items = []
    fields = _FINANCIAL_FIELDS[report_type]
    for row in response["data"]["item"]:
        if row.get("thscode") != meta["thscode"] or row.get("currency") != "CNY":
            raise SourceError("hithink: statement identity/currency mismatch")
        if row.get("period") != ("annual" if period == "annual" else "quarterly"):
            raise SourceError("hithink: statement period mismatch")
        values = {key: number(row.get(key)) for key in fields}
        if all(value is None for value in values.values()):
            raise SourceError("hithink: financial fields missing")
        items.append({**row, **values, "报告期": date_ms(row.get("period_end_ms"))})
    if not items:
        raise SourceError("hithink: financial summary missing")
    items.sort(key=lambda row: row["报告期"], reverse=True)
    return {
        "ok": True,
        "error": None,
        "items": items[:num],
        "detail": "summary",
        "field_schema": "hithink",
        "amount_unit": "CNY",
        "eps_unit": "CNY per share",
        "period_basis": "balance at period end; income/cashflow year-to-date",
        "requested_count": num,
        "returned_count": min(num, len(items)),
        "partial": len(items) < num,
        **provenance(response),
    }
