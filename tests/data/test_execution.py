"""Real local coordination and data reuse, without external network requests."""

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pytest
import requests
from scutio_data._runtime import cache as cache_module
from scutio_data._runtime import execution
from scutio_data._runtime.config import set_setting
from scutio_data._runtime.execution import CacheSpec, SourceFailure, execute
from scutio_data._runtime.timeouts import RequestCancelled, RequestTimeout, request_timeout
from scutio_data.paths import cache_dir, state_dir


def settings(**overrides):
    set_setting("execution", {"providers": {"fixture": overrides}})


def test_source_slots_allow_independent_requests_together():
    settings(max_inflight=3)
    barrier = threading.Barrier(3)

    def fetch(index):
        barrier.wait(timeout=3)
        return {"value": index}

    with ThreadPoolExecutor(3) as pool:
        results = list(
            pool.map(
                lambda i: execute("fixture", "quote", partial(fetch, i), parameters=i), range(3)
            )
        )
    assert [row["value"] for row in results] == [0, 1, 2]


def test_cache_hit_does_not_wait_for_a_busy_source():
    settings(max_inflight=1)
    spec = CacheSpec(60)
    original = execute("fixture", "cached", lambda: {"value": 7, "as_of": "2026-01-01"}, cache=spec)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        assert release.wait(3)
        return {}

    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(execute, "fixture", "slow", slow)
        assert started.wait(3)
        try:
            with request_timeout(0.2):
                cached = execute(
                    "fixture", "cached", lambda: pytest.fail("cache missed"), cache=spec
                )
            assert cached == original
        finally:
            release.set()
        pending.result()


def test_same_request_shares_validated_result_even_when_cache_is_unwritable(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return {"rows": [{"value": 3}]}

    def unwritable(*args, **kwargs):
        raise PermissionError("read-only cache")

    monkeypatch.setattr(cache_module, "private_write", unwritable)
    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(execute, "fixture", "same", fetch, cache=CacheSpec(60))
        assert entered.wait(3)
        waiter = pool.submit(execute, "fixture", "same", fetch, cache=CacheSpec(60))
        time.sleep(0.05)
        release.set()
        first, second = owner.result(), waiter.result()
    assert len(calls) == 1
    first["rows"][0]["value"] = 99
    assert second["rows"][0]["value"] == 3


def test_cache_identity_preserves_period_key_and_never_persists_parameters():
    calls = []

    def fetch():
        calls.append(1)
        return {"value": len(calls)}

    spec = CacheSpec(60, identity={"adapter_version": 1})
    for period, key in [("annual", "a"), ("quarter", "a"), ("annual", "b"), ("annual", "a")]:
        execute(
            "fixture",
            "income",
            fetch,
            parameters={"period": period, "token": "private-fixture-param"},
            credential_scope=key,
            cache=spec,
        )
    assert len(calls) == 3
    for path in [*cache_dir().rglob("*.json"), *state_dir().rglob("*.json")]:
        assert "private-fixture-param" not in path.read_text(encoding="utf-8")


def test_invalid_cached_fields_are_refetched():
    spec = CacheSpec(60, validator=lambda value: isinstance(value.get("rows"), list))
    execute("fixture", "rows", lambda: {"rows": [1]}, cache=spec)
    path = next((cache_dir() / "api/responses/fixture").glob("*.json"))
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["value"] = {"rows": "wrong shape"}
    path.write_text(json.dumps(cached))
    assert execute("fixture", "rows", lambda: {"rows": [2]}, cache=spec) == {"rows": [2]}
    with pytest.raises(SourceFailure, match="validation"):
        execute("fixture", "bad", lambda: {"rows": None}, cache=spec)


def test_endpoint_failure_does_not_block_another_capability_and_empty_is_success():
    settings(failure_threshold=3)
    calls = []

    def failure():
        calls.append(1)
        raise SourceFailure("upstream_connection_error")

    for _ in range(3):
        with pytest.raises(SourceFailure, match="upstream_connection_error"):
            execute("fixture", "fund_flow", failure)
    with pytest.raises(SourceFailure, match="cooldown"):
        execute("fixture", "fund_flow", failure)
    assert len(calls) == 3
    assert execute("fixture", "financial", lambda: []) == []
    assert execute("fixture", "financial", lambda: []) == []


def test_only_consecutive_same_kind_failures_trigger_the_threshold():
    settings(failure_threshold=3)
    for code in ("upstream_connection_error", "proxy_error", "upstream_connection_error"):

        def failure(code=code):
            raise SourceFailure(code)

        with pytest.raises(SourceFailure, match=code):
            execute("fixture", "a", failure)
    assert execute("fixture", "a", lambda: 1) == 1


def test_heavy_family_queue_does_not_reserve_the_other_provider_slot():
    settings(max_inflight=2)
    started, release = threading.Event(), threading.Event()

    def heavy():
        started.set()
        assert release.wait(3)
        return 1

    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(
            execute, "fixture", "heavy", heavy, parameters=1, family="scan", max_inflight=1
        )
        assert started.wait(3)
        waiting = pool.submit(
            execute, "fixture", "heavy", lambda: 2, parameters=2, family="scan", max_inflight=1
        )
        try:
            with request_timeout(0.3):
                assert execute("fixture", "light", lambda: 3) == 3
        finally:
            release.set()
        assert owner.result() == 1
        assert waiting.result() == 2


def test_http_rate_limit_preserves_response_and_stops_other_endpoint():
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "90"
    assert execute("fixture", "a", lambda: response) is response
    with pytest.raises(SourceFailure, match="cooldown"):
        execute("fixture", "b", lambda: pytest.fail("provider cooldown ignored"))
    state = execution.health_snapshot("fixture")
    assert state["cooldown_until"] > time.time() + 60


def test_permission_is_key_and_endpoint_scoped():
    def denied():
        raise SourceFailure("permission")

    with pytest.raises(SourceFailure):
        execute("fixture", "a", denied, credential_scope="key-a")
    with pytest.raises(SourceFailure, match="cooldown"):
        execute("fixture", "a", denied, credential_scope="key-a")
    assert execute("fixture", "b", lambda: 1, credential_scope="key-a") == 1
    assert execute("fixture", "a", lambda: 2, credential_scope="key-b") == 2


@pytest.mark.parametrize("error", [RequestTimeout("queue_wait"), RequestCancelled()])
def test_control_exit_does_not_poison_source_health(error):
    settings(failure_threshold=1)

    def interrupted():
        raise error

    with pytest.raises(type(error)):
        execute("fixture", "a", interrupted)
    assert execution.health_snapshot("fixture")["failures"] == {}
    assert execute("fixture", "a", lambda: 1) == 1


def test_only_one_recovery_probe_can_use_an_expired_cooldown():
    settings(failure_threshold=1)

    def failure():
        raise SourceFailure("upstream_connection_error")

    with pytest.raises(SourceFailure):
        execute("fixture", "a", failure)
    path = next((state_dir() / "execution-v1/fixture").glob("*/health.json"))
    state = json.loads(path.read_text(encoding="utf-8"))
    for entry in state["failures"].values():
        entry["until"] = time.time() - 1
    path.write_text(json.dumps(state))
    started, release = threading.Event(), threading.Event()

    def probe():
        started.set()
        assert release.wait(3)
        return 1

    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(execute, "fixture", "a", probe, parameters=1)
        assert started.wait(3)
        try:
            with pytest.raises(SourceFailure, match="probe"):
                execute("fixture", "a", lambda: pytest.fail("second probe"), parameters=2)
        finally:
            release.set()
        assert first.result() == 1
    assert execute("fixture", "a", lambda: 2) == 2


def test_recovery_is_rechecked_after_waiting_for_source_slots(monkeypatch):
    """A request which queued before a cooldown must not bypass the recovery probe."""
    from contextlib import contextmanager

    from scutio_data._runtime.coordination import digest, try_lock

    original = execution.permit
    root = state_dir() / "execution-v1/fixture/public"
    key = "route:" + digest(["a", "default"])

    @contextmanager
    def queued(*args, **kwargs):
        with original(*args, **kwargs):
            health = root / "health.json"
            health.write_text(
                json.dumps(
                    {"failures": {key: {"code": "upstream_timeout", "until": time.time() - 1}}}
                )
            )
            with try_lock(root / "probes" / (digest(key) + ".lock")) as acquired:
                assert acquired
                yield

    monkeypatch.setattr(execution, "permit", queued)
    with pytest.raises(SourceFailure, match="probe"):
        execute("fixture", "a", lambda: pytest.fail("queued caller bypassed recovery probe"))


_CHILD = """
import json, os, sys, time
from pathlib import Path
from scutio_data._runtime.execution import execute, CacheSpec
root = Path(sys.argv[1])
index = sys.argv[2]
(root / ('ready-' + index)).touch()
while not (root / 'go').exists():
    time.sleep(.01)
def call():
    with (root / 'events').open('a') as out:
        out.write(json.dumps(['start', time.monotonic(), index]) + '\\n')
    time.sleep(.2)
    with (root / 'events').open('a') as out:
        out.write(json.dumps(['end', time.monotonic(), index]) + '\\n')
    return {'value': 1}
same = sys.argv[3] == 'same'
execute('fixture', 'endpoint', call, parameters=0 if same else index,
        cache=CacheSpec(60) if same else None)
"""


@pytest.mark.parametrize("same", [False, True])
def test_processes_share_actual_slots_start_rate_and_exact_cache(tmp_path, same):
    settings(max_inflight=2, starts_per_second=10)
    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "skills/scutio/scripts")
    children = [
        subprocess.Popen(
            [sys.executable, str(script), str(tmp_path), str(i), "same" if same else "different"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(4)
    ]
    try:
        until = time.monotonic() + 10
        while len(list(tmp_path.glob("ready-*"))) != 4:
            assert time.monotonic() < until, "children failed to reach barrier"
            time.sleep(0.02)
        (tmp_path / "go").touch()
        for child in children:
            _, error = child.communicate(timeout=10)
            assert child.returncode == 0, error
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
    events = sorted(
        (json.loads(row) for row in (tmp_path / "events").read_text(encoding="utf-8").splitlines()),
        key=lambda row: row[1],
    )
    starts = [stamp for kind, stamp, _ in events if kind == "start"]
    assert len(starts) == (1 if same else 4)
    active = peak = 0
    for kind, _, _ in events:
        active += 1 if kind == "start" else -1
        peak = max(peak, active)
    assert peak <= 2 and active == 0
    if not same:
        assert peak == 2
        assert min(b - a for a, b in zip(starts, starts[1:])) >= 0.09
