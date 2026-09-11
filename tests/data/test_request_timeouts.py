"""Real cancellation and shared budgets, with no external network requests."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from scutio_data import data_sources, fundamentals, market, source_pref
from scutio_data._providers import eastmoney, quotes
from scutio_data._providers.akshare import client as akshare
from scutio_data._providers.hithink import client as hithink
from scutio_data._runtime import http, timeouts
from scutio_data.paths import cache_dir, state_dir


def test_http_worker_starts_from_installed_copy_without_pythonpath(tmp_path):
    scripts = Path(__file__).parents[2] / "skills" / "scutio" / "scripts"
    installed = tmp_path / "installed"
    shutil.copytree(
        scripts / "scutio_data",
        installed / "scutio_data",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    script = r"""
import sys
sys.path.insert(0, sys.argv[1])
import requests
from scutio_data._runtime.http import Session
with Session() as session:
    session.trust_env = False
    try:
        session.get('scutio-test://no-network')
    except requests.exceptions.InvalidSchema as exc:
        print(type(exc).__name__ + ': ' + str(exc))
    else:
        raise AssertionError('invalid scheme should be rejected')
"""
    # The parent launches the production worker unchanged. An unsupported URL
    # scheme exercises imports and JSON IPC without DNS or a network connection.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(installed)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "InvalidSchema: HTTP transfer failed (InvalidSchema)"


@pytest.mark.parametrize(
    "returncode,stdout,stderr,expected",
    [
        (1, "", "ModuleNotFoundError: sensitive-detail", "cause=ModuleNotFoundError"),
        (1, "", "unknown crash: sensitive-detail", "cause=unknown"),
        (0, "sensitive-detail", "", "invalid JSON"),
        (0, "[]", "", "invalid response"),
    ],
)
def test_http_worker_failure_distinguishes_startup_from_transfer_without_raw_output(
    monkeypatch, returncode, stdout, stderr, expected
):
    monkeypatch.setattr(
        http.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], returncode, stdout, stderr),
    )
    with http.Session() as session, pytest.raises(requests.RequestException) as failure:
        session.get("scutio-test://no-network")
    assert expected in str(failure.value)
    assert "sensitive-detail" not in str(failure.value)


def test_http_worker_spawn_failure_is_sanitized(monkeypatch):
    monkeypatch.setattr(
        http.subprocess, "run", Mock(side_effect=FileNotFoundError(2, "sensitive-detail"))
    )
    with http.Session() as session, pytest.raises(requests.RequestException) as failure:
        session.get("scutio-test://no-network")
    assert str(failure.value) == "HTTP worker could not start (errno=2)"


def test_nested_operations_share_deadline_and_override_defaults(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(timeouts.time, "monotonic", lambda: clock[0])
    with timeouts.request_timeout(200, source_seconds=90):
        with timeouts.operation_budget("query"), timeouts.source_budget("query"):
            assert timeouts.remaining() == 90
            clock[0] += 20
            with timeouts.operation_budget("history"), timeouts.source_budget("history"):
                assert timeouts.remaining() == 70
        with timeouts.source_budget("history"):
            assert timeouts.remaining() == 90
        clock[0] = 301
        with pytest.raises(timeouts.RequestTimeout):
            timeouts.remaining()
    with timeouts.operation_budget("query"), timeouts.source_budget("query"):
        assert timeouts.remaining() == 30


def test_source_timeout_releases_budget_for_fallback(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(timeouts.time, "monotonic", lambda: clock[0])
    monkeypatch.setenv(data_sources.KEY_NAME, "synthetic-test-key")

    def stalled(_):
        with timeouts.source_budget("query"):
            clock[0] += 31
            timeouts.remaining("response")

    def recovered(_):
        with timeouts.source_budget("query"):
            assert timeouts.remaining() == 29
            return {"sh600519": {"price": 10}}

    monkeypatch.setattr(hithink, "quotes", stalled)
    monkeypatch.setattr(quotes, "tencent_quote", recovered)
    result = market.security_quote("600519")
    assert result["ok"] and result["sources_used"] == ["tencent"]
    assert "response" in result["errors"]["hithink"]


def test_queue_timeout_does_not_mark_credential_unhealthy(monkeypatch):
    monkeypatch.setenv(data_sources.KEY_NAME, "synthetic-test-key")
    network = Mock(side_effect=AssertionError("queued call must not send credentials"))
    monkeypatch.setattr(hithink, "Session", network)
    hithink._lock.acquire()
    try:
        with (
            timeouts.request_timeout(0.05),
            pytest.raises(timeouts.RequestTimeout, match="queue_wait"),
        ):
            hithink.request("/api/test", {})
    finally:
        hithink._lock.release()
    assert not list(state_dir().rglob("health.json"))
    network.assert_not_called()


@pytest.mark.parametrize("lock_kind", ["process", "thread"])
def test_optional_source_preference_lock_cannot_block_successful_data(monkeypatch, lock_kind):
    path = source_pref._path()
    before = path.read_bytes()
    release = threading.Event()
    locked = threading.Event()
    holder = None
    worker = None
    if lock_kind == "process":
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from scutio_data._runtime.storage import file_lock
with file_lock(sys.argv[2]):
    print('locked', flush=True)
    sys.stdin.readline()
"""
        worker = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                script,
                str(Path(akshare.__file__).parents[3]),
                str(path.with_suffix(path.suffix + ".lock")),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    else:

        def hold():
            with source_pref._lock:
                locked.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
    done = threading.Event()
    outcome = {}
    query = None
    try:
        if worker is not None:
            assert worker.stdout.readline().strip() == "locked"
        else:
            assert locked.wait(3)
        rows = [{"item": "股票代码", "value": "600519"}, {"item": "股票简称", "value": "贵州茅台"}]
        monkeypatch.setattr(akshare, "fetch", Mock(return_value=rows))
        fallback = Mock(side_effect=AssertionError("a busy preference must not discard data"))
        monkeypatch.setattr(quotes, "tencent_quote", fallback)

        def request():
            try:
                with timeouts.request_timeout(0.05):
                    outcome["result"] = fundamentals.stock_info("600519")
            except Exception as exc:
                outcome["error"] = exc
            finally:
                done.set()

        query = threading.Thread(target=request)
        query.start()
        # The holder cannot release until this assertion finishes. The old
        # blocking implementation therefore fails without relying on a sleep.
        assert done.wait(1), "optional preference state blocked a completed data request"
        assert "error" not in outcome
        assert outcome["result"]["ok"] and outcome["result"]["symbol"] == "sh600519"
        fallback.assert_not_called()
        assert path.read_bytes() == before
        with pytest.raises(TimeoutError, match="busy"):
            source_pref.clear_pref()
    finally:
        release.set()
        if worker is not None:
            worker.communicate("release\n", timeout=5)
        if holder is not None:
            holder.join(5)
        if query is not None:
            query.join(5)
    source_pref.mark_ok("stock_info:a", "eastmoney")
    assert source_pref.get_pref("stock_info:a")["last_ok"] == "eastmoney"


_SLOW_HTTP = r"""
import importlib.util, json, socket, sys, threading, time
import urllib3.util.connection
from pathlib import Path
spec = importlib.util.spec_from_file_location("worker", Path(sys.argv[1]) / "_http_worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
client, server = socket.socketpair()
def serve():
    with server:
        request = b""
        while b"\r\n\r\n" not in request:
            request += server.recv(4096)
        body = b'{"code":0,"data":{"item":[]}}'
        count = int(sys.argv[2])
        server.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nSet-Cookie: test=ok\r\nContent-Length: ' + str(count + len(body)).encode() + b'\r\n\r\n')
        Path(sys.argv[3]).write_text('response started')
        for _ in range(count):
            server.sendall(b' ')
            time.sleep(0.03)
        server.sendall(body)
threading.Thread(target=serve, daemon=True).start()
urllib3.util.connection.create_connection = lambda *a, **k: client
payload = json.load(sys.stdin)
payload['url'] = 'http://offline.invalid'
payload['trust_env'] = False
print(json.dumps(worker.transfer(payload)))
"""


def test_slow_http_body_is_killed_and_releases_hithink_lock(monkeypatch, tmp_path):
    monkeypatch.setenv(data_sources.KEY_NAME, "synthetic-test-key")
    real_run = subprocess.run
    attempted = []

    def run(args, **kwargs):
        attempted.append(args)
        # Change only the worker's network connector. Real requests reads a slow
        # socketpair response in a real child; no DNS, listening port or server.
        return real_run(
            [
                sys.executable,
                "-c",
                _SLOW_HTTP,
                str(Path(args[-1]).parent),
                "500",
                str(tmp_path / "started"),
            ],
            **kwargs,
        )

    monkeypatch.setattr(http.subprocess, "run", run)
    started = time.monotonic()
    with (
        timeouts.request_timeout(2, source_seconds=2),
        pytest.raises(timeouts.RequestTimeout, match="response"),
    ):
        hithink.request("/api/test", {}, ttl=30)
    assert time.monotonic() - started < 5
    assert (tmp_path / "started").exists()
    assert len(attempted) == 1
    assert hithink._lock.acquire(blocking=False)
    hithink._lock.release()
    assert not [p for p in (cache_dir() / "api").rglob("*.json") if p.name != "health.json"]
    health = json.loads(next(state_dir().rglob("health.json")).read_text())
    assert not health.get("verified_capabilities")
    # A new request can acquire the same cross-process lock and finish.
    response = Mock(status_code=200, headers={})
    response.json.return_value = {"code": 0, "data": {"item": []}}
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    monkeypatch.setattr(hithink, "Session", lambda: session)
    assert hithink.request("/api/test", {})["source"] == "hithink"


def test_http_worker_returns_complete_response_and_cookies(monkeypatch, tmp_path):
    real_run = subprocess.run

    def run(args, **kwargs):
        payload = json.loads(kwargs["input"])
        assert payload["options"]["timeout"] == [5, 20]
        assert payload["options"]["allow_redirects"] is False
        assert payload["headers"]["X-api-key"] == "synthetic-test-key"
        assert "synthetic-test-key" not in " ".join(args)
        assert "HITHINK_FINANCE_API_KEY" not in kwargs["env"]
        return real_run(
            [
                sys.executable,
                "-c",
                _SLOW_HTTP,
                str(Path(args[-1]).parent),
                "0",
                str(tmp_path / "started"),
            ],
            **kwargs,
        )

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(http.subprocess, "run", run)
    with http.Session() as session:
        session.headers.update({"X-api-key": "synthetic-test-key"})
        response = session.get("http://offline.invalid", allow_redirects=False)
        assert response.status_code == 200
        assert response.json() == {"code": 0, "data": {"item": []}}
        assert response.encoding == "utf-8" and response.content
        assert session.cookies.get("test") == "ok"


@pytest.mark.parametrize("status,wait,attempts", [(429, "2", 2), (429, "100", 1), (403, "2", 1)])
def test_http_retries_respect_server_wait_and_operation_deadline(
    monkeypatch, status, wait, attempts
):
    clock = [100.0]
    monkeypatch.setattr(timeouts.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        timeouts.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    request = Mock(return_value=Mock(status_code=status, headers={"Retry-After": wait}))
    monkeypatch.setattr(eastmoney.EM_SESSION, "request", request)
    monkeypatch.setattr(eastmoney, "_em_reserve_start", lambda: None)
    with timeouts.request_timeout(10):
        if wait == "100":
            with pytest.raises(timeouts.RequestTimeout, match="retry_wait"):
                eastmoney.em_get("https://offline.invalid")
        else:
            assert eastmoney.em_get("https://offline.invalid").status_code == status
    assert request.call_count == attempts
    assert clock[0] == (102 if attempts == 2 else 100)


def test_calendar_timeout_keeps_completed_days(monkeypatch):
    from scutio_data import macro

    clock = [100.0]
    monkeypatch.setattr(timeouts.time, "monotonic", lambda: clock[0])
    calls = []

    def fetch(function, **params):
        calls.append(params["date"])
        if len(calls) == 2:
            clock[0] += 181
            timeouts.remaining("response")
        clock[0] += 120
        return [{"日期": "2026-09-09", "事件": "CPI", "公布": 0.2, "预期": 0.1}]

    monkeypatch.setattr(akshare, "fetch", fetch)
    result = macro.economic_calendar("2026-09-09", days=3)
    assert result["ok"] and result["partial"] and result["completed_days"] == 1
    assert result["items"][0]["surprise"] == 0.1
    assert "timeout" in result["errors"]["2026-09-10"]
    assert calls == ["20260909", "20260910"]
