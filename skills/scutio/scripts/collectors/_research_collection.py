"""统一的按问题采集与显式复用；每个模块保留自己的数据和观察记录。"""

from datetime import datetime, timezone

from _collection_modules import build_requests, normalize_modules
from scutio_data import data_sources
from scutio_data.core import canonical_symbol, market_of, result_err

SCHEMA_VERSION = 2


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def collect(
    code=None,
    *,
    question,
    modules,
    depth="light",
    period="annual",
    filing_kind="annual",
    peers=None,
    reuse=None,
) -> dict:
    requested = normalize_modules(modules)
    symbol = canonical_symbol(code) if code is not None else None
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question is required")
    if depth not in ("light", "full"):
        raise ValueError("depth must be light|full")
    if period not in ("annual", "all", "quarter", "cumulative"):
        raise ValueError("unsupported financial period")
    if filing_kind not in ("annual", "semi", "q1", "q3", "all"):
        raise ValueError("unsupported filing kind")
    if peers is not None and "peers" not in requested:
        raise ValueError("peer mapping requires the peers module")
    requests = build_requests(
        symbol, requested, depth=depth, period=period, filing_kind=filing_kind, peers=peers
    )
    # Dependencies are reused only if already supplied or explicitly selected.
    dependencies = {request.input_module for request in requests.values() if request.input_module}
    dependency_requests = build_requests(
        symbol, dependencies, depth=depth, period=period, filing_kind=filing_kind
    )
    if reuse is not None:
        if not isinstance(reuse, dict) or reuse.get("collector_schema_version") != SCHEMA_VERSION:
            raise ValueError("reuse must be a current question-scoped collection result")
        if reuse.get("code") != symbol:
            raise ValueError("reuse code does not match requested security")
        if not isinstance(reuse.get("modules"), dict) or not isinstance(
            reuse.get("observations"), dict
        ):
            raise ValueError("reuse modules and observations must be objects")
    reuse = reuse or {}
    routing = data_sources.reuse_context()
    out = {
        "collector_schema_version": SCHEMA_VERSION,
        "code": symbol,
        "market": market_of(symbol) if symbol else None,
        "question": question.strip(),
        "collection_started_at": _timestamp(),
        "requested_modules": requested,
        "modules": {},
        "observations": {},
        "errors": {},
        "data_routing": routing,
    }

    def reusable(name):
        if reuse.get("data_routing") != routing:
            return None
        payload = reuse.get("modules", {}).get(name)
        observation = reuse.get("observations", {}).get(name)
        request = requests.get(name) or dependency_requests.get(name)
        if not isinstance(payload, dict) or payload.get("ok") is not True or request is None:
            return None
        if not isinstance(observation, dict) or observation.get("request") != request.parameters:
            return None
        try:
            stamp = datetime.fromisoformat(observation["collected_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
                return None
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        return payload, observation

    ordered = sorted(requested, key=lambda name: requests[name].input_module is not None)
    for name in ordered:
        request = requests[name]
        dependency = request.input_module
        input_result = None
        if dependency:
            if dependency in out["modules"]:
                input_result = out["modules"][dependency], out["observations"][dependency]
            else:
                input_result = reusable(dependency)
        prior = reusable(name)
        if (
            prior
            and input_result
            and (
                prior[1].get("input_collected_at") != input_result[1]["collected_at"]
                or dependency in out["observations"]
                and not out["observations"][dependency]["reused"]
            )
        ):
            prior = None
        if prior:
            payload, observation = prior
            out["modules"][name] = payload
            out["observations"][name] = {**observation, "reused": True}
        else:
            try:
                kwargs = (
                    {request.input_argument: input_result[0] if input_result else None}
                    if dependency
                    else {}
                )
                payload = request.fetch(**kwargs)
                if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
                    payload = result_err("invalid_result_envelope", source=name)
            except Exception as exc:
                payload = result_err(f"{type(exc).__name__}: {exc}", source=name)
            out["modules"][name] = payload
            out["observations"][name] = {
                "collected_at": _timestamp(),
                "reused": False,
                "request": request.parameters,
            }
            if dependency:
                out["observations"][name].update(
                    input_module=dependency,
                    input_collected_at=input_result[1]["collected_at"] if input_result else None,
                )
        if payload.get("ok") is False:
            out["errors"][name] = payload.get("error") or "unknown_error"
    succeeded = [name for name in requested if out["modules"][name]["ok"]]
    out["ok"] = bool(succeeded)
    out["partial"] = bool(succeeded) and (
        bool(out["errors"])
        or any(
            out["modules"][name].get("partial") or out["modules"][name].get("stale")
            for name in succeeded
        )
    )
    out["collection_completed_at"] = _timestamp()
    out["note"] = "采集状态不代表研究结论；各模块保留原始时点、覆盖范围与缺口。"
    return out
