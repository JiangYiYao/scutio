"""Real worker cancellation, pipe responses and nested budgets; no public network."""

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
from scutio_data import data_sources, market
from scutio_data._providers import quotes
from scutio_data._providers.akshare import client as akshare
from scutio_data._providers.hithink import client as hithink
from scutio_data._runtime import http, processes, timeouts

SCRIPTS = Path(__file__).parents[2] / "skills" / "scutio" / "scripts"


@pytest.mark.parametrize(
    "worker",
    ["_runtime/_http_worker.py", "_providers/akshare/_akshare_worker.py"],
)
def test_offline_guard_blocks_real_network_worker_before_spawn(worker):
    with pytest.raises(RuntimeError, match="mock the network worker boundary"):
        processes.managed_run(
            [sys.executable, "-B", str(SCRIPTS / "scutio_data" / worker)],
            input="{}",
            timeout=1,
        )


def test_offline_worker_exception_only_allows_exact_local_command(local_worker_command):
    command = local_worker_command(
        [sys.executable, "-B", "-c", "print('local-only')", "_http_worker.py"]
    )
    assert processes.managed_run(command, timeout=5).stdout.strip() == "local-only"
    with pytest.raises(RuntimeError, match="mock the network worker boundary"):
        processes.managed_run([*command, "different-command"], timeout=1)


def test_http_worker_starts_from_installed_copy_without_pythonpath(tmp_path):
    installed = tmp_path / "installed"
    shutil.copytree(
        SCRIPTS / "scutio_data",
        installed / "scutio_data",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    script = """
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
def test_http_worker_failure_is_sanitized(monkeypatch, returncode, stdout, stderr, expected):
    monkeypatch.setattr(
        http,
        "managed_run",
        lambda *a, **k: subprocess.CompletedProcess([], returncode, stdout, stderr),
    )
    with http.Session() as session, pytest.raises(requests.RequestException) as failure:
        session.get("scutio-test://no-network")
    assert expected in str(failure.value)
    assert "sensitive-detail" not in str(failure.value)


def test_http_worker_spawn_failure_is_sanitized(monkeypatch):
    monkeypatch.setattr(
        http, "managed_run", Mock(side_effect=FileNotFoundError(2, "sensitive-detail"))
    )
    with http.Session() as session, pytest.raises(requests.RequestException) as failure:
        session.get("scutio-test://no-network")
    assert str(failure.value) == "HTTP worker could not start (errno=2)"


def test_http_retry_exhaustion_preserves_proxy_error_without_entering_direct_route(monkeypatch):
    failure = requests.exceptions.ProxyError("synthetic-proxy-error")
    transport = Mock(side_effect=[requests.exceptions.ConnectionError("unreachable"), failure])
    monkeypatch.setattr(http.Session, "_transport_request", transport)
    with http.Session() as session, pytest.raises(requests.exceptions.ProxyError) as raised:
        session.get("https://push2.eastmoney.com/api/qt/stock/get", _source_attempts=2)
    assert raised.value is failure
    assert transport.call_count == 2
    assert all(call.kwargs["network_trust_env"] for call in transport.call_args_list)


def test_nested_operations_share_deadline_and_override_defaults(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(timeouts.time, "monotonic", lambda: clock[0])
    with timeouts.request_timeout(200, source_seconds=90, connect_seconds=12, read_seconds=70):
        with timeouts.operation_budget("query"), timeouts.source_budget("query"):
            assert timeouts.remaining() == 90
            assert timeouts.transport_timeout() == (12, 70)
            clock[0] += 20
            with timeouts.operation_budget("history"), timeouts.source_budget("history"):
                assert timeouts.remaining() == 70
                with timeouts.request_timeout(500, connect_seconds=400, read_seconds=400):
                    assert timeouts.transport_timeout() == (70, 70)
        with timeouts.source_budget("history"):
            assert timeouts.remaining() == 90
        clock[0] = 301
        with pytest.raises(timeouts.RequestTimeout):
            timeouts.remaining()
    with timeouts.operation_budget("query"), timeouts.source_budget("query"):
        assert timeouts.remaining() == 30
        assert timeouts.transport_timeout() == (5, 20)


@pytest.mark.parametrize(
    "field", ["total_seconds", "source_seconds", "connect_seconds", "read_seconds"]
)
@pytest.mark.parametrize("value", [True, 0, -1, float("inf"), float("nan")])
def test_timeout_parameters_must_be_positive_finite(field, value):
    values = {"total_seconds": 60, field: value}
    with pytest.raises(ValueError), timeouts.request_timeout(**values):
        pass


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


_SLOW_HTTP = r"""
import importlib.util, json, socket, sys, threading, time
import urllib3.util.connection
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parents[1]))
from scutio_data._runtime.processes import install_worker_guard
install_worker_guard()
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


def _socket_worker(monkeypatch, tmp_path, count, local_worker_command, payload_check=None):
    real_run = processes.managed_run

    def run(args, **kwargs):
        if payload_check:
            payload_check(json.loads(kwargs["input"]), args, kwargs)
        return real_run(
            local_worker_command(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    _SLOW_HTTP,
                    str(Path(args[-1]).parent),
                    str(count),
                    str(tmp_path / "started"),
                ]
            ),
            **kwargs,
        )

    monkeypatch.setattr(http, "managed_run", run)


def test_slow_http_body_is_killed_at_total_deadline(monkeypatch, tmp_path, local_worker_command):
    _socket_worker(monkeypatch, tmp_path, 500, local_worker_command)
    started = time.monotonic()
    with (
        timeouts.request_timeout(2),
        http.Session() as session,
        pytest.raises(timeouts.RequestTimeout, match="response"),
    ):
        session.get("http://offline.invalid")
    assert time.monotonic() - started < 5
    assert (tmp_path / "started").exists()


def test_http_worker_returns_complete_response_cookies_and_transport_overrides(
    monkeypatch, tmp_path, local_worker_command
):
    def check(payload, args, kwargs):
        assert payload["options"]["timeout"] == [9, 25]
        assert payload["options"]["allow_redirects"] is False
        assert payload["headers"]["X-api-key"] == "synthetic-test-key"
        assert "synthetic-test-key" not in " ".join(args)
        assert "HITHINK_FINANCE_API_KEY" not in kwargs["env"]

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "synthetic-test-key")
    _socket_worker(monkeypatch, tmp_path, 0, local_worker_command, check)
    with (
        timeouts.request_timeout(60, connect_seconds=9, read_seconds=25),
        http.Session() as session,
    ):
        session.headers.update({"X-api-key": "synthetic-test-key"})
        response = session.get("http://offline.invalid", timeout=(1, 2), allow_redirects=False)
        assert response.status_code == 200
        assert response.json() == {"code": 0, "data": {"item": []}}
        assert response.encoding == "utf-8" and response.content
        assert session.cookies.get("test") == "ok"


_TREE_WORKER = r"""
import json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scutio_data._runtime.processes import install_worker_guard
install_worker_guard()
child = subprocess.Popen([sys.executable, '-c', 'import sys,time; from pathlib import Path; p=Path(sys.argv[1]);\nwhile True: p.write_text(str(time.time_ns())); time.sleep(0.02)', sys.argv[2]])
Path(sys.argv[3]).write_text(json.dumps({'child':child.pid}))
while True: time.sleep(1)
"""


def _wait_for_file(path, seconds=5):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if path.exists() and path.stat().st_size:
            return
        time.sleep(0.02)
    raise AssertionError("test worker did not reach its ready point")


def test_cancelled_worker_reaps_its_live_descendant(tmp_path):
    marker, ready = tmp_path / "heartbeat", tmp_path / "ready"
    cancel = timeouts.CancellationToken()
    errors = []

    def run():
        try:
            with timeouts.request_timeout(20, cancel_event=cancel):
                processes.managed_run(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        _TREE_WORKER,
                        str(SCRIPTS),
                        str(marker),
                        str(ready),
                    ]
                )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        _wait_for_file(ready)
        _wait_for_file(marker)
    finally:
        cancel.cancel()
        thread.join(5)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], timeouts.RequestCancelled)
    before = marker.read_bytes()
    time.sleep(0.15)
    assert marker.read_bytes() == before, "descendant remained active after cancellation returned"


def test_abrupt_parent_exit_stops_managed_worker_tree(tmp_path):
    marker, ready = tmp_path / "heartbeat", tmp_path / "ready"
    supervisor = """
import sys
sys.path.insert(0, sys.argv[1])
from scutio_data._runtime.processes import managed_run
managed_run([sys.executable, '-B', '-c', sys.argv[2], sys.argv[1], sys.argv[3], sys.argv[4]], timeout=20)
"""
    parent = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-c",
            supervisor,
            str(SCRIPTS),
            _TREE_WORKER,
            str(marker),
            str(ready),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_file(ready)
        _wait_for_file(marker)
    finally:
        parent.kill()
        parent.communicate(timeout=5)
    time.sleep(0.3)
    before = marker.read_bytes()
    time.sleep(0.15)
    assert marker.read_bytes() == before, "parent exit left a live descendant"


def test_cancellation_interrupts_wait_without_becoming_provider_failure():
    token = timeouts.CancellationToken()
    token.cancel()
    with timeouts.cancellation_scope(token), pytest.raises(timeouts.RequestCancelled):
        timeouts.pause(60, "queue_wait")
    assert not issubclass(timeouts.RequestCancelled, Exception)


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
