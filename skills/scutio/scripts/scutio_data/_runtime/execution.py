"""Shared source execution: precise reuse, quotas and scoped recovery.

Adapters own source semantics. This module never selects research material or
changes a successful domain result to fit a performance target.
"""

from __future__ import annotations

import copy
import math
import os
import re
import threading
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from scutio_data._runtime import config
from scutio_data._runtime.cache import CacheSpec, read_api_cache, write_api_cache
from scutio_data._runtime.coordination import (
    digest,
    locked,
    permit,
    try_lock,
    update_json,
    wait,
)
from scutio_data._runtime.storage import read_json
from scutio_data._runtime.timeouts import (
    RequestTimeout,
    add_timing,
    check_cancelled,
    observe_event,
    pause,
    remaining,
    source_budget,
)
from scutio_data.paths import cache_dir, state_dir

_active_source = ContextVar("scutio_active_source", default=False)
_http_attempts = ContextVar("scutio_http_attempts", default=None)
_flights = {}
_flight_guard = threading.Lock()
_RETRYABLE = {"transient", "upstream_connection_error", "upstream_timeout"}
_NETWORK = _RETRYABLE | {"proxy_error", "tls_error"}


class SourceFailure(RuntimeError):
    def __init__(self, code, message=None, *, retry_after=None, scope="endpoint"):
        self.code = code
        self.retry_after = retry_after
        self.scope = scope
        super().__init__(message or code)


@dataclass
class _Attempts:
    maximum: int
    used: int = 0


@contextmanager
def attempt_budget(maximum=2):
    """Share physical HTTP attempts across provider retries and proxy recovery."""
    current = _http_attempts.get()
    token = None
    if current is None:
        current = _Attempts(maximum)
        token = _http_attempts.set(current)
    try:
        yield current
    finally:
        if token is not None:
            _http_attempts.reset(token)


def http_transfer_started():
    remaining("response")
    budget = _http_attempts.get()
    if budget is not None:
        if budget.used >= budget.maximum:
            raise SourceFailure("attempts_exhausted", "HTTP attempt budget exhausted")
        budget.used += 1


@dataclass(frozen=True)
class Policy:
    max_inflight: int = 4
    starts_per_second: float = 0
    failure_threshold: int = 3
    cooldown_seconds: float = 60

    @property
    def interval(self):
        return 1 / self.starts_per_second if self.starts_per_second else 0


_DEFAULTS = {
    "hithink": Policy(4, 4),
    "eastmoney": Policy(2, 1),
    "sec": Policy(4, 1 / 0.15),
    "unknown": Policy(2),
}


def source_policy(provider):
    baseline = _DEFAULTS.get(provider, Policy())
    settings = config.settings().get("execution", {})
    if not isinstance(settings, dict):
        raise ValueError("execution settings must be an object")
    providers = settings.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("execution.providers must be an object")
    overrides = providers.get(provider, {})
    if not isinstance(overrides, dict) or set(overrides) - set(Policy.__dataclass_fields__):
        raise ValueError("unsupported source execution settings")
    values = {name: getattr(baseline, name) for name in Policy.__dataclass_fields__}
    values.update(overrides)
    for name in ("max_inflight", "failure_threshold"):
        value = values[name]
        if type(value) is not int or not 1 <= value <= 64:
            raise ValueError(name + " must be an integer between 1 and 64")
    for name in ("starts_per_second", "cooldown_seconds"):
        value = values[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(name + " must be a nonnegative finite number")
    return Policy(**values)


def in_source_attempt():
    return bool(_active_source.get())


def _root(provider, credential_scope=None):
    if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", provider):
        raise ValueError("invalid source identifier")
    scope = digest(credential_scope) if credential_scope is not None else "public"
    return state_dir() / "execution-v1" / provider / scope


def network_identity():
    names = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
    return digest({key: os.environ.get(key) for name in names for key in (name, name.upper())})


def http_identity(url):
    parsed = urlsplit(str(url))
    host = (parsed.hostname or "").lower()
    domains = {
        "eastmoney.com": "eastmoney",
        "dfcfw.com": "eastmoney",
        "gtimg.cn": "tencent",
        "qq.com": "tencent",
        "sina.com.cn": "sina",
        "sinajs.cn": "sina",
        "sina.cn": "sina",
        "cninfo.com.cn": "cninfo",
        "sec.gov": "sec",
        "aicubes.cn": "hithink",
        "cls.cn": "cls",
        "baidu.com": "baidu",
        "jin10.com": "jin10",
        "legulegu.com": "legu",
        "csindex.com.cn": "csindex",
        "10jqka.com.cn": "ths_public",
        "mofcom.gov.cn": "mofcom",
        "sse.com.cn": "sse",
        "szse.cn": "szse",
        "bse.cn": "bse",
    }
    for domain, provider in domains.items():
        if host == domain or host.endswith("." + domain):
            return provider, parsed.path or "/"
    return None


def _number(value, default=0):
    return value if type(value) in (int, float) and math.isfinite(value) else default


def _scopes(endpoint, family, route):
    return ("provider", "endpoint:" + endpoint, "route:" + digest([family or endpoint, route]))


def _entries(state):
    return state.get("failures", {}) if isinstance(state.get("failures"), dict) else {}


def _cooldown(entry, now):
    return max(0, _number(entry.get("until")) - now) if isinstance(entry, dict) else 0


@contextmanager
def _health_gate(root, scopes):
    path = root / "health.json"
    with ExitStack() as probes:
        held = set()

        def recheck():
            entries = _entries(read_json(path))
            for key in scopes:
                entry = entries.get(key, {})
                if not isinstance(entry, dict):
                    continue
                delay = _cooldown(entry, time.time())
                if delay:
                    raise SourceFailure(
                        "cooldown",
                        "source cooldown (" + str(entry.get("code", "unavailable")) + ")",
                        retry_after=delay,
                    )
                if _number(entry.get("until")) and key not in held:
                    acquired = probes.enter_context(
                        try_lock(root / "probes" / (digest(key) + ".lock"))
                    )
                    if not acquired:
                        raise SourceFailure("cooldown", "source recovery probe in progress")
                    held.add(key)
                    current = _entries(read_json(path)).get(key, {})
                    delay = _cooldown(current, time.time())
                    if delay:
                        raise SourceFailure("cooldown", "source cooldown", retry_after=delay)

        recheck()
        yield recheck


def _failure_code(exc):
    if isinstance(exc, RequestTimeout):
        return None
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code
    if isinstance(exc, requests.exceptions.ProxyError):
        return "proxy_error"
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error"
    if isinstance(exc, requests.exceptions.Timeout):
        return "upstream_timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "upstream_connection_error"
    if isinstance(exc, requests.exceptions.ChunkedEncodingError):
        return "transient"
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status", None)
    return _status_code(status)


def _status_code(status):
    if type(status) is not int:
        return None
    if status == 401:
        return "authentication"
    if status == 403:
        return "permission"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "transient"
    if status >= 400:
        return "request_rejected"
    return None


def _record_failure(root, endpoint, scopes, exc, policy):
    code = _failure_code(exc)
    if code not in _NETWORK | {"authentication", "permission", "rate_limited"}:
        return
    key = (
        scopes[0]
        if code == "authentication"
        or code == "rate_limited"
        and getattr(exc, "scope", None) == "provider"
        else scopes[1]
        if code == "permission"
        else scopes[2]
    )

    def update(state):
        now = time.time()
        failures = state.setdefault("failures", {})
        if not isinstance(failures, dict):
            failures = state["failures"] = {}
        previous = failures.get(key, {})
        if not isinstance(previous, dict):
            previous = {}
        recent = now - _number(previous.get("at")) < 300 and previous.get("code") == code
        count = int(_number(previous.get("count"))) + 1 if recent else 1
        delay = (
            300
            if code in ("authentication", "permission")
            else max(0, _number(getattr(exc, "retry_after", None), 30))
            if code == "rate_limited"
            else policy.cooldown_seconds
            if count >= policy.failure_threshold or _number(previous.get("until"))
            else 0
        )
        failures[key] = {
            "code": code,
            "count": count,
            "at": now,
            "until": now + delay if delay else 0,
            "endpoint": endpoint,
        }

    update_json(root / "health.json", update)


def _record_success(root, endpoint, scopes):
    def update(state):
        now = time.time()
        failures = _entries(state)
        for key in scopes:
            entry = failures.get(key, {})
            # An in-flight success must not erase another request's new auth/429 block.
            if not _cooldown(entry, now):
                failures.pop(key, None)
        state["failures"] = failures
        verified = state.setdefault("verified_capabilities", {})
        if not isinstance(verified, dict):
            verified = state["verified_capabilities"] = {}
        verified[endpoint] = datetime.now(timezone.utc).isoformat()

    update_json(root / "health.json", update, required=False)


def health_snapshot(provider, *, credential_scope=None):
    state = read_json(_root(provider, credential_scope) / "health.json")
    failures = _entries(state)
    now = time.time()
    provider_entry = failures.get("provider", {})
    return {
        "verified_capabilities": state.get("verified_capabilities", {}),
        "cooldown_until": provider_entry.get("until") if _cooldown(provider_entry, now) else None,
        "capability_cooldowns": {
            key.removeprefix("endpoint:"): entry["until"]
            for key, entry in failures.items()
            if key.startswith("endpoint:") and _cooldown(entry, now)
        },
        "failures": copy.deepcopy(failures),
    }


def reset_health(provider, *, credential_scope=None):
    def reset(state):
        state.pop("failures", None)
        state.pop("routes", None)

    update_json(_root(provider, credential_scope) / "health.json", reset)


def preferred_route(provider, endpoint, network_id):
    routes = read_json(_root(provider) / "health.json").get("routes", {})
    entry = routes.get(digest([endpoint, network_id]), {}) if isinstance(routes, dict) else {}
    if isinstance(entry, dict) and _number(entry.get("until")) > time.time():
        return entry.get("route") if entry.get("route") in ("direct", "environment") else None
    return None


def remember_route(provider, endpoint, network_id, route, ttl=300):
    if route not in ("direct", "environment"):
        raise ValueError("invalid network route")

    def update(state):
        routes = state.setdefault("routes", {})
        if not isinstance(routes, dict):
            routes = state["routes"] = {}
        now = time.time()
        for key, value in list(routes.items()):
            if not isinstance(value, dict) or _number(value.get("until")) <= now:
                routes.pop(key, None)
        routes[digest([endpoint, network_id])] = {"route": route, "until": now + ttl}

    update_json(_root(provider) / "health.json", update, required=False)


@dataclass
class _Flight:
    ready: threading.Event = field(default_factory=threading.Event)
    value: object = None
    error: BaseException | None = None


def _source_call(provider, root, endpoint, call, *, route, family, attempts, max_inflight):
    policy = source_policy(provider)
    scopes = _scopes(endpoint, family, route)
    with _health_gate(root, scopes) as recheck, attempt_budget(2) as transfers:
        for attempt in range(attempts):
            error, value = None, None
            with permit(
                root,
                max_inflight=policy.max_inflight,
                interval=policy.interval,
                family=family or endpoint,
                family_limit=max_inflight,
            ):
                # A cooldown may have started or expired while this request queued.
                recheck()
                token = _active_source.set(True)
                started = time.monotonic()
                observe_event(
                    "request",
                    provider=provider,
                    endpoint=endpoint,
                    route=route,
                    attempt=attempt + 1,
                )
                try:
                    value = call()
                    code = _status_code(getattr(value, "status_code", None))
                    if code:
                        from scutio_data._runtime.http import retry_after

                        error = SourceFailure(
                            code,
                            retry_after=retry_after(value.headers.get("Retry-After")),
                            scope="provider" if code == "rate_limited" else "endpoint",
                        )
                except Exception as exc:
                    error = exc
                finally:
                    _active_source.reset(token)
                    add_timing("attempt_seconds", time.monotonic() - started)
            if error is None:
                return value
            if (
                _failure_code(error) in _RETRYABLE
                and attempt + 1 < attempts
                and transfers.used < transfers.maximum
            ):
                pause(0.5, "retry_wait")
                continue
            _record_failure(root, endpoint, scopes, error, policy)
            if value is not None:
                return value  # Preserve requests.Response semantics for HTTP callers.
            raise error
    raise AssertionError("source attempt did not finish")


def execute(
    provider,
    endpoint,
    call,
    *,
    parameters=None,
    credential_scope=None,
    route="default",
    family=None,
    attempts=1,
    cache: CacheSpec | None = None,
    max_inflight=None,
):
    """Run an explicit source request; cache hits bypass source locks and cooldowns."""
    if type(attempts) is not int or not 1 <= attempts <= 2:
        raise ValueError("source attempts must be 1 or 2")
    if max_inflight is not None and (type(max_inflight) is not int or max_inflight < 1):
        raise ValueError("source family max_inflight must be positive")
    root = _root(provider, credential_scope)
    identity = {
        "schema": 1,
        "provider": provider,
        "endpoint": endpoint,
        "parameters": parameters,
        "credential": credential_scope,
        "route": route,
        "mode": config.mode(),
        "version": cache.version if cache else None,
        "identity": cache.identity if cache else None,
    }
    request_key = digest(identity)
    # Only fingerprints, never parameters/URLs/credentials, are persisted in identity.
    cache_path = cache_dir() / "api" / "responses" / provider / (request_key + ".json")

    def cached():
        found, value = read_api_cache(cache_path, request_key, cache) if cache else (False, None)
        if found:
            observe_event("cache_hit", provider=provider, endpoint=endpoint)
        return found, value

    with source_budget():
        hit, value = cached()
        if hit:
            return value
        flight_key = str(root / "requests" / request_key)
        while True:
            check_cancelled()
            with _flight_guard:
                flight = _flights.get(flight_key)
                owner = flight is None
                if owner:
                    flight = _flights[flight_key] = _Flight()
            if owner:
                break
            while not flight.ready.is_set():
                wait(0.02, "singleflight_wait")
            if flight.error is None:
                observe_event("request_shared", provider=provider, endpoint=endpoint)
                return copy.deepcopy(flight.value)
            if isinstance(flight.error, Exception) and not isinstance(flight.error, RequestTimeout):
                raise flight.error
            # The owner was cancelled or exhausted its own deadline; retry with ours.
            remaining("singleflight_wait")

        try:
            with locked(root / "requests" / (request_key + ".lock"), stage="singleflight_wait"):
                hit, value = cached()
                if not hit:
                    value = _source_call(
                        provider,
                        root,
                        endpoint,
                        call,
                        route=route,
                        family=family,
                        attempts=attempts,
                        max_inflight=max_inflight,
                    )
                    failed_response = _status_code(getattr(value, "status_code", None))
                    if cache and not failed_response:
                        if cache.validator is not None and cache.validator(value) is False:
                            raise SourceFailure(
                                "invalid_response", "source cache validation failed"
                            )
                        check_cancelled()
                        write_api_cache(
                            cache_path,
                            {"identity": request_key, "saved_at": time.time(), "value": value},
                            ttl=cache.ttl,
                        )
                    if not failed_response:
                        _record_success(root, endpoint, _scopes(endpoint, family, route))
                flight.value = copy.deepcopy(value)
                return value
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with _flight_guard:
                if _flights.get(flight_key) is flight:
                    del _flights[flight_key]
                flight.ready.set()


__all__ = [
    "CacheSpec",
    "SourceFailure",
    "execute",
    "health_snapshot",
    "reset_health",
    "http_identity",
    "in_source_attempt",
    "network_identity",
    "preferred_route",
    "remember_route",
    "source_policy",
    "attempt_budget",
    "http_transfer_started",
]
