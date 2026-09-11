"""Repeated-failure diagnostics stay advisory, bounded and credential-free."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scutio_data._providers.akshare import client as akshare_source
from scutio_data._providers.akshare import maintenance as maintenance
from scutio_data._providers.akshare.errors import AKShareError


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("SCUTIO_AKSHARE_UPDATE_CHECK", "1")
    monkeypatch.setattr(maintenance, "installed_version", lambda: "1.18.94")
    launch = Mock()
    monkeypatch.setattr(maintenance.subprocess, "Popen", launch)
    return launch


def failure():
    return AKShareError("provider_error", error_type="KeyError")


def test_threshold_counts_complete_calls_and_resets_on_success(runtime):
    for _ in range(2):
        assert maintenance.observe("stock_news_em", failure()) is None
    runtime.assert_not_called()
    maintenance.observe("stock_news_em")
    assert maintenance.status()["failures"] == {}
    for _ in range(3):
        maintenance.observe("stock_news_em", failure())
    assert runtime.call_count == 1
    for _ in range(4):
        maintenance.observe("stock_news_em", failure())
    assert runtime.call_count == 1  # global daily check cooldown
    assert maintenance.status()["check"]["state"] == "pending"


@pytest.mark.parametrize(
    "code,status",
    [
        ("proxy_error", None),
        ("upstream_connection_error", None),
        ("upstream_timeout", None),
        ("total_timeout", None),
        ("rate_limited", 429),
        ("upstream_api_error", 400),
        ("upstream_api_error", 401),
        ("upstream_api_error", 403),
    ],
)
def test_network_auth_rate_failures_never_trigger_version_checks(runtime, code, status):
    for _ in range(4):
        maintenance.observe("stock_news_em", AKShareError(code, status=status))
    runtime.assert_not_called()
    assert not maintenance.status()["failures"]


def test_interfaces_are_counted_separately_and_version_change_resets(runtime, monkeypatch):
    for function in ("stock_news_em", "stock_info_global_cls"):
        for _ in range(2):
            maintenance.observe(function, failure())
    runtime.assert_not_called()
    monkeypatch.setattr(maintenance, "installed_version", lambda: "1.18.95")
    maintenance.observe("stock_news_em", failure())
    assert maintenance.status()["failures"]["stock_news_em"]["count"] == 1


def test_old_failures_expire(runtime, monkeypatch):
    now = [100000]
    monkeypatch.setattr(maintenance.time, "time", lambda: now[0])
    maintenance.observe("stock_news_em", failure())
    maintenance.observe("stock_news_em", failure())
    now[0] += 3601
    maintenance.observe("stock_news_em", failure())
    assert maintenance.status()["failures"]["stock_news_em"]["count"] == 1
    runtime.assert_not_called()


def test_check_is_bounded_and_never_installs(runtime, monkeypatch):
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"state": "update_available", "latest_compatible": "1.19.0", "fix_verified": False}
            ),
        )
    )
    monkeypatch.setattr(maintenance.subprocess, "run", run)
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "private-test-key")
    result = maintenance.check_now()
    assert result["state"] == "update_available" and result["fix_verified"] is False
    assert 0 < run.call_args.kwargs["timeout"] <= 4
    assert "HITHINK_FINANCE_API_KEY" not in run.call_args.kwargs["env"]
    assert "--probe" in run.call_args.args[0] and "pip" not in run.call_args.args[0]
    assert maintenance.status()["policy"] == "check_only"


def test_failed_update_check_does_not_replace_data_failure(runtime, monkeypatch):
    monkeypatch.setattr(
        maintenance.subprocess, "run", Mock(side_effect=TimeoutError("private-url"))
    )
    assert maintenance.check_now()["state"] == "check_failed"
    assert "private" not in json.dumps(maintenance.status())


def test_successful_empty_result_is_not_failure(runtime, monkeypatch):
    monkeypatch.setattr(akshare_source, "_fetch", lambda *a, **k: [])
    assert akshare_source.fetch("stock_news_em") == []
    runtime.assert_not_called()


def test_release_probe_skips_yanked_prerelease_and_incompatible_python(runtime, monkeypatch):
    import urllib.request
    from io import BytesIO

    releases = {
        v: [{"yanked": yanked, "requires_python": python}]
        for v, yanked, python in [
            ("1.18.95", False, ">=3.11"),
            ("1.18.96", True, ">=3.11"),
            ("1.19.0rc1", False, ">=3.11"),
            ("1.19.1", False, ">=99"),
        ]
    }
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: BytesIO(json.dumps({"releases": releases}).encode()),
    )
    result = maintenance._probe()
    assert result["state"] == "update_available" and result["latest_compatible"] == "1.18.95"
    assert result["fix_verified"] is False


def test_abandoned_background_check_does_not_stay_pending_forever(runtime, monkeypatch):
    for _ in range(3):
        maintenance.observe("stock_news_em", failure())
    requested = maintenance.status()["check"]["requested_at"]
    monkeypatch.setattr(maintenance.time, "time", lambda: requested + 121)
    assert maintenance.status()["check"]["state"] == "check_failed"


def test_metadata_route_recovery_uses_remaining_budget(runtime, monkeypatch):
    monkeypatch.setattr(maintenance.time, "monotonic", Mock(side_effect=[100, 100, 104]))
    run = Mock(
        side_effect=[
            TimeoutError(),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"state": "up_to_date", "latest_compatible": "1.18.94"}),
            ),
        ]
    )
    monkeypatch.setattr(maintenance.subprocess, "run", run)
    assert maintenance.check_now()["network"] == "direct"
    first, second = run.call_args_list
    assert first.kwargs["timeout"] == second.kwargs["timeout"] == 4
    assert second.kwargs["env"]["NO_PROXY"] == "*"


def test_success_does_not_rewrite_unchanged_health_state(runtime, monkeypatch):
    maintenance.observe("stock_news_em")
    save = Mock(wraps=maintenance._save)
    monkeypatch.setattr(maintenance, "_save", save)
    maintenance.observe("stock_news_em")
    maintenance.observe("stock_info_global_cls")
    save.assert_not_called()
    maintenance.observe("stock_news_em", failure())
    maintenance.observe("stock_news_em")
    assert save.call_count == 2
