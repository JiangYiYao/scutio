"""Bounded proxy recovery and safe distinctions between transport/auth failures."""

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from scutio_data._providers.akshare import client as akshare_source
from scutio_data._providers.akshare.errors import AKShareError, safe_failure


def reply(code=None, **fields):
    body = (
        {"ok": True, "items": [{"price": 10}]}
        if code is None
        else {"ok": False, "failure": {"code": code, **fields}}
    )
    return SimpleNamespace(stdout=json.dumps(body), returncode=0)


@pytest.mark.parametrize("function,budget", [("stock_hk_hist", 60), ("stock_repurchase_em", 120)])
def test_proxy_recovery_is_confined_to_worker_and_shares_deadline(monkeypatch, function, budget):
    monkeypatch.setenv("SCUTIO_AKSHARE_NETWORK", "auto")
    monkeypatch.setenv("https_proxy", "http://user:private-proxy-password@localhost:9999")
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "private-financial-key")
    clock = [100.0]

    def run(*args, **kwargs):
        clock[0] += 8
        return reply("proxy_error") if clock[0] == 108 else reply()

    runner = Mock(side_effect=run)
    monkeypatch.setattr(akshare_source.subprocess, "run", runner)
    monkeypatch.setattr(akshare_source.time, "monotonic", lambda: clock[0])
    assert akshare_source.fetch(function) == [{"price": 10}]
    first, second = runner.call_args_list
    assert first.kwargs["timeout"] == budget and second.kwargs["timeout"] == budget - 8
    assert "https_proxy" in first.kwargs["env"]
    assert "https_proxy" not in second.kwargs["env"]
    assert second.kwargs["env"]["NO_PROXY"] == second.kwargs["env"]["no_proxy"] == "*"
    assert "HITHINK_FINANCE_API_KEY" not in first.kwargs["env"]
    assert "HITHINK_FINANCE_API_KEY" not in second.kwargs["env"]
    assert os.environ["https_proxy"].endswith("localhost:9999")


@pytest.mark.parametrize(
    "code", ["upstream_api_error", "rate_limited", "upstream_connection_error", "provider_error"]
)
def test_non_proxy_failures_are_not_retried(monkeypatch, code):
    monkeypatch.setenv("SCUTIO_AKSHARE_NETWORK", "auto")
    runner = Mock(return_value=reply(code, status=400))
    monkeypatch.setattr(akshare_source.subprocess, "run", runner)
    with pytest.raises(AKShareError) as caught:
        akshare_source.fetch("stock_hk_hist", symbol="00700")
    assert caught.value.code == code and runner.call_count == 1


@pytest.mark.parametrize("mode", ["environment", "direct"])
def test_forced_route_never_silently_changes_network(monkeypatch, mode):
    monkeypatch.setenv("SCUTIO_AKSHARE_NETWORK", mode)
    runner = Mock(return_value=reply("proxy_error"))
    monkeypatch.setattr(akshare_source.subprocess, "run", runner)
    with pytest.raises(AKShareError) as caught:
        akshare_source.fetch("stock_hk_hist")
    assert runner.call_count == 1 and caught.value.attempts == [
        {"network": mode, "code": "proxy_error"}
    ]


def test_proxy_then_direct_failure_reports_both_routes(monkeypatch):
    monkeypatch.setenv("SCUTIO_AKSHARE_NETWORK", "auto")
    monkeypatch.setattr(
        akshare_source.subprocess,
        "run",
        Mock(side_effect=[reply("proxy_error"), reply("upstream_connection_error")]),
    )
    with pytest.raises(AKShareError) as caught:
        akshare_source.fetch("stock_hk_hist")
    assert caught.value.code == "upstream_connection_error"
    assert [item["code"] for item in caught.value.attempts] == [
        "proxy_error",
        "upstream_connection_error",
    ]


def test_exhausted_budget_never_starts_second_worker(monkeypatch):
    monkeypatch.setenv("SCUTIO_AKSHARE_NETWORK", "auto")
    clock = [100.0]
    monkeypatch.setattr(akshare_source.time, "monotonic", lambda: clock[0])

    def run(*args, **kwargs):
        clock[0] += 61
        return reply("proxy_error")

    runner = Mock(side_effect=run)
    monkeypatch.setattr(akshare_source.subprocess, "run", runner)
    with pytest.raises(AKShareError, match=r"60(?:\.0)?s"):
        akshare_source.fetch("stock_hk_hist")
    assert runner.call_count == 1


def test_api_failure_does_not_leak_upstream_message():
    class APIError(Exception):
        status_code = 400

    failure = safe_failure(APIError("private-cookie-value"))
    assert failure == {"code": "upstream_api_error", "status": 400, "type": "APIError"}
    assert "private-cookie-value" not in json.dumps(failure)


@pytest.mark.parametrize("function", ["stock_individual_spot_xq", "stock_hk_spot_em"])
def test_disabled_quote_sources_never_start_worker(monkeypatch, function):
    runner = Mock()
    monkeypatch.setattr(akshare_source.subprocess, "run", runner)
    with pytest.raises(ValueError, match="unsupported AKShare adapter"):
        akshare_source.fetch(function)
    runner.assert_not_called()


def test_nested_proxy_error_is_distinguished_without_leaking_url():
    wrapped = RuntimeError("contains-private-url")
    wrapped.__cause__ = requests.exceptions.ProxyError("https://user:password@proxy")
    failure = safe_failure(wrapped)
    assert failure["code"] == "proxy_error"
    assert "password" not in json.dumps(failure) and "private" not in json.dumps(failure)
