"""Bounded official Financial API REST transport; no SDK or public-source recursion."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import requests

from scutio_data._runtime import config
from scutio_data._runtime.cache import write_api_cache
from scutio_data._runtime.http import Session, retry_after
from scutio_data._runtime.symbols import is_a_share as eligible
from scutio_data._runtime.symbols import split_code
from scutio_data._runtime.timeouts import RequestTimeout, pause, remaining, source, source_budget
from scutio_data.paths import cache_dir, state_dir

BASE = "https://fuyao.aicubes.cn"


_lock = threading.Lock()


class SourceError(RuntimeError):
    """Only locally constructed, credential-free errors may leave the transport."""


def preferred(code):
    return config.enabled() and eligible(code)


def namespace(key):
    return hashlib.sha256(key.encode()).hexdigest()[:24]


@contextmanager
def _exclusive(path, deadline):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise RequestTimeout("queue_wait")
    try:
        with open(path, "a+b") as stream:
            if os.name == "nt":
                import msvcrt

                stream.write(b"0")
                stream.flush()
            else:
                import fcntl
            while True:
                try:
                    if os.name == "nt":
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RequestTimeout("queue_wait") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)
    finally:
        _lock.release()


def request(path, params, *, ttl=0):
    with source_budget():
        return _request(path, params, ttl=ttl)


def _request(path, params, *, ttl=0):
    if not config.enabled():
        raise SourceError("hithink: disabled or key not configured")
    key = config.credential()[0]
    root = cache_dir() / "api" / "hithink" / namespace(key)
    state_root = state_dir() / "hithink" / namespace(key)
    token = hashlib.sha256(json.dumps([2, path, params], sort_keys=True).encode()).hexdigest()
    cache_path = root / (token + ".json")
    state_path = state_root / "health.json"
    deadline = time.monotonic() + remaining("queue_wait")
    # One in-flight request per credential, across threads and local processes.
    with _exclusive(state_root / "request.lock", deadline):
        cached = config.read_json(cache_path)
        if ttl and time.time() - cached.get("saved_at", 0) < ttl:
            return cached["value"]
        state = config.read_json(state_path)
        until = max(state.get("cooldown", 0), state.get(path, 0))
        if until > time.time():
            raise SourceError("hithink: cooldown (%s)" % state.get("reason", "retry_later"))
        with Session() as session:
            session.headers.update({"X-api-key": key, "Accept": "application/json"})
            for attempt in range(2):
                wait = max(0, state.get("last_start", 0) + 1 - time.time())
                pause(wait, "rate_wait")
                state["last_start"] = time.time()
                config.private_write(state_path, json.dumps(state))
                try:
                    response = session.get(
                        BASE + path,
                        params=params,
                        timeout=(min(5, remaining()), min(20, remaining())),
                        allow_redirects=False,
                    )
                    status = response.status_code
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    code = payload.get("code") if isinstance(payload, dict) else None
                except requests.RequestException:
                    status, code, payload = 503, None, {}
                if 200 <= status < 300 and type(code) is int and code == 0:
                    data = payload.get("data")
                    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
                        raise SourceError("hithink: invalid response schema")
                    timestamp = data.get("timestamp")
                    try:
                        data_time = (
                            datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()
                            if timestamp is not None
                            else None
                        )
                    except (TypeError, ValueError, OverflowError, OSError):
                        raise SourceError("hithink: invalid source timestamp") from None
                    value = {
                        "data": data,
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "data_as_of": data_time,
                        "source": "hithink",
                    }
                    state.setdefault("verified_capabilities", {})[path] = value["retrieved_at"]
                    config.private_write(state_path, json.dumps(state))
                    if ttl:
                        write_api_cache(
                            cache_path, {"saved_at": time.time(), "value": value}, ttl=ttl
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
                if reason in ("authentication", "permission", "rate_limited"):
                    seconds = (
                        retry_after(response.headers.get("Retry-After"))
                        if reason == "rate_limited"
                        else 300
                    )
                    state[path if reason == "permission" else "cooldown"] = time.time() + seconds
                    state["reason"] = reason
                    config.private_write(state_path, json.dumps(state))
                if reason != "transient" or attempt == 1:
                    raise SourceError("hithink: " + reason)
                pause(0.5, "retry_wait")
    raise SourceError("hithink: request failed")


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
    return {key: response[key] for key in ("source", "retrieved_at", "data_as_of")}


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
                "timestamp_basis": "latest metric update; individual metrics may differ",
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
    fields = {
        "lrb": "basic_eps operating_income operating_costs operating_expenses operating_profit profit_total net_profit parent_holder_net_profit income_tax_expense interest_expenses manage_fee sales_fee research_and_development_expenses",
        "fzb": "total_current_assets non_current_nets_total assets_total total_debt holder_equity_total cash accounts_receivable",
        "llb": "act_cash_flow_net invest_cash_flow_net financing_cash_flow_net cash_equivalents_net_addition pay_dividends_profits_interest_cash pay_fixed_assets_etc_cash",
    }[report_type].split()
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
