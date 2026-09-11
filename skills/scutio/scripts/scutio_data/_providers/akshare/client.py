"""Selected AKShare adapters, isolated by a killable total request deadline.

AKShare functions may perform multiple unbounded HTTP calls. A worker process
keeps this behaviour from hanging the caller; no global requests monkeypatch."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from contextlib import nullcontext
from importlib.metadata import version
from pathlib import Path

from scutio_data._providers.akshare.errors import AKShareError
from scutio_data._providers.akshare.network import network_identity, network_mode, worker_env
from scutio_data._providers.akshare.registry import ADAPTERS, ALLOWED
from scutio_data._runtime.execution import (
    CacheSpec,
    SourceFailure,
    execute,
    preferred_route,
    remember_route,
)
from scutio_data._runtime.processes import managed_run
from scutio_data._runtime.timeouts import RequestTimeout, remaining, request_timeout, source_budget


def fetch(function: str, *, _timeout_seconds: float | None = None, _snapshot=False, **params):
    kind = "batch" if function in ("stock_repurchase_em", "stock_ggcg_em") else None
    with source_budget(kind):
        return _observed_fetch(
            function,
            _timeout_seconds=_timeout_seconds,
            **({"_snapshot": True} if _snapshot else {}),
            **params,
        )


def _observed_fetch(function, *, _timeout_seconds=None, **params):
    if function not in ALLOWED:
        raise ValueError("unsupported AKShare adapter")
    from scutio_data._providers.akshare import maintenance as akshare_maintenance

    try:
        rows = (
            _fetch(function, **params)
            if _timeout_seconds is None
            else _fetch(function, _timeout_seconds=_timeout_seconds, **params)
        )
    except AKShareError as exc:
        check = akshare_maintenance.observe(function, exc)
        if check:
            exc.update_check = check
            if check.get("state") == "update_available":
                exc.args = (
                    str(exc)
                    + "; newer AKShare available: "
                    + check["latest_compatible"]
                    + " (fix unverified)",
                )
            elif check.get("state") == "pending":
                exc.args = (str(exc) + "; repeated adapter failure: checking AKShare releases",)
        raise
    akshare_maintenance.observe(function)
    return rows


def _valid_payload(value):
    return (
        isinstance(value, dict)
        and type(value.get("retrieved_at")) in (int, float)
        and math.isfinite(value["retrieved_at"])
        and isinstance(value.get("items"), list)
        and all(isinstance(row, dict) for row in value["items"])
    )


def _worker(function, params, route, maximum):
    seconds = min(remaining("response"), maximum)
    try:
        result = managed_run(
            [sys.executable, "-B", str(Path(__file__).with_name("_akshare_worker.py"))],
            input=json.dumps({"function": function, "params": params}),
            text=True,
            capture_output=True,
            timeout=seconds,
            env=worker_env(direct=route == "direct"),
            check=False,
            stage="worker",
        )
    except subprocess.TimeoutExpired:
        raise AKShareError("total_timeout", timeout_seconds=seconds) from None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise AKShareError("worker_failed") from None
    if not isinstance(payload, dict):
        raise AKShareError("invalid_response")
    if payload.get("ok"):
        value = {"items": payload.get("items"), "retrieved_at": time.time()}
        if not _valid_payload(value):
            raise AKShareError("invalid_response")
        return value
    failure = payload.get("failure") or {}
    raise AKShareError(
        failure.get("code", "provider_error"),
        error_type=failure.get("type"),
        status=failure.get("status"),
    )


def _fetch(function, *, _timeout_seconds=None, _snapshot=False, **params):
    with request_timeout(_timeout_seconds) if _timeout_seconds is not None else nullcontext():
        return _routed_fetch(
            function, _timeout_seconds=_timeout_seconds, _snapshot=_snapshot, **params
        )


def _routed_fetch(function, *, _timeout_seconds=None, _snapshot=False, **params):
    if function not in ADAPTERS:
        raise ValueError("unsupported AKShare adapter")
    adapter = ADAPTERS[function]
    mode = network_mode()
    network_id = network_identity()
    endpoint = "akshare:" + function
    preferred = preferred_route(adapter.provider, endpoint, network_id) if mode == "auto" else None
    routes = ["direct"] if mode == "direct" or preferred == "direct" else ["environment"]
    if mode == "auto" and not preferred:
        routes.append("direct")
    attempts = []
    budget = remaining("response")
    if _timeout_seconds is not None:
        if (
            isinstance(_timeout_seconds, bool)
            or not math.isfinite(float(_timeout_seconds))
            or float(_timeout_seconds) <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        budget = min(budget, float(_timeout_seconds))
    started = time.monotonic()
    wall_start = time.time()
    for route in routes:
        left = min(remaining("response"), budget - (time.monotonic() - started))
        if left <= 0:
            raise AKShareError("total_timeout", attempts=attempts, timeout_seconds=budget)
        try:
            value = execute(
                adapter.provider,
                endpoint,
                lambda: _worker(function, params, route, left),
                parameters=params,
                route=network_id + ":" + route,
                family=adapter.family,
                max_inflight=1 if adapter.heavy_scan else None,
                cache=CacheSpec(
                    ttl=3600,
                    version=2,
                    identity={"akshare_version": version("akshare")},
                    validator=_valid_payload,
                )
                if _snapshot
                else None,
            )
        except RequestTimeout as exc:
            raise AKShareError("total_timeout", attempts=attempts, timeout_seconds=budget) from exc
        except SourceFailure as exc:
            attempts.append({"network": route, "code": exc.code})
            if (
                mode == "auto"
                and route == "environment"
                and exc.code in ("proxy_error", "tls_error")
            ):
                continue
            raise AKShareError(
                exc.code,
                error_type=getattr(exc, "error_type", None),
                status=getattr(exc, "status", None),
                attempts=attempts,
                timeout_seconds=budget,
            ) from None
        if mode == "auto" and route == "direct":
            remember_route(adapter.provider, endpoint, network_id, "direct")
        if _snapshot:
            return value["items"], {
                "snapshot_retrieved_at": value["retrieved_at"],
                "snapshot_cached": value["retrieved_at"] < wall_start,
                "snapshot_ttl_seconds": 3600,
            }
        return value["items"]
    raise AKShareError("total_timeout", attempts=attempts, timeout_seconds=budget)
