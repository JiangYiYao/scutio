"""Source routing, credential isolation and normalization regression contracts."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import scutio_data.capital.dividends as capital_dividends
from scutio_data import capital, data_sources, fundamentals, market, valuation
from scutio_data._providers import quotes as quote_source
from scutio_data._providers.akshare import client as akshare_source
from scutio_data._providers.akshare import market as akshare_market
from scutio_data._providers.hithink import client as hithink
from scutio_data._providers.hithink import parse as hithink_parse
from scutio_data.paths import cache_dir

FIXTURES = Path(__file__).parents[1] / "fixtures/hithink"


def raw(name):
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def response(data=None, *, code=0, status=200, headers=None):
    return SimpleNamespace(
        status_code=status,
        headers=headers or {},
        json=lambda: {"code": code, "data": data, "message": "secret must not leave upstream"},
    )


def transport(monkeypatch, responses):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.side_effect = responses
    monkeypatch.setattr(hithink, "Session", lambda: session)
    monkeypatch.setenv(data_sources.KEY_NAME, "test-credential")
    return session


def test_no_key_or_public_mode_never_sends_credentials(monkeypatch):
    session = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(hithink, "Session", session)
    assert not data_sources.enabled()
    with pytest.raises(hithink.SourceError):
        hithink.request("/api/meta/tickers/search", {})
    monkeypatch.setenv(data_sources.KEY_NAME, "test-key")
    monkeypatch.setenv("SCUTIO_DATA_MODE", "public")
    with pytest.raises(hithink.SourceError):
        hithink.request("/api/meta/tickers/search", {})
    session.assert_not_called()


def test_private_configuration_and_hint(monkeypatch):
    assert data_sources.hint("hk") is None
    assert data_sources.hint("a")
    assert data_sources.hint("a") is None
    data_sources.configure("file-test-key")
    assert data_sources.credential() == ("file-test-key", "credentials_file")
    if os.name != "nt":
        assert (data_sources.home() / "credentials.env").stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv(data_sources.KEY_NAME, "environment-test-key")
    assert data_sources.credential()[0] == "environment-test-key"
    assert "environment-test-key" not in json.dumps(data_sources.status())


def test_agent_configuration_via_stdin_persists_without_echoing_key():
    """Agent stdin configuration survives a new process and respects public mode."""
    script = Path(__file__).parents[2] / "skills/scutio/scripts/data_sources.py"
    key = "stdin-test-credential"
    data_sources.set_setting("mode", "public")
    configured = subprocess.run(
        [sys.executable, str(script), "configure", "--stdin"],
        input=key + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    checked = subprocess.run(
        [sys.executable, str(script), "status"],
        text=True,
        capture_output=True,
        check=True,
    )
    for result in (configured, checked):
        assert key not in result.stdout + result.stderr
        status = json.loads(result.stdout)
        assert status["hithink_configured"] is True
        assert status["credential_source"] == "credentials_file"
        assert status["hithink_enabled"] is False
        assert status["mode"] == "public"
    assert data_sources.credential() == (key, "credentials_file")
    if os.name != "nt":
        assert (data_sources.home() / "credentials.env").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "code", ["hk00700", "usAAPL", "sh000001", "sz399001", "sh510300", "sh110093"]
)
def test_asset_boundary_precedes_auth_network(code, monkeypatch):
    monkeypatch.setenv(data_sources.KEY_NAME, "test-key")
    request = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(hithink, "request", request)
    assert not hithink.preferred(code)
    with pytest.raises(hithink.SourceError):
        hithink.identity(code)
    request.assert_not_called()


@pytest.mark.parametrize(
    "status,code,reason",
    [
        (401, 2001, "authentication"),
        (403, 2003, "permission"),
        (429, 4001, "rate_limited"),
        (200, 3002, "data_not_ready"),
        (200, 1003, "request_rejected"),
        (302, None, "request_rejected"),
    ],
)
def test_http_and_business_errors_are_not_success_or_secret_leaks(
    monkeypatch, status, code, reason
):
    session = transport(
        monkeypatch, [response(code=code, status=status, headers={"Retry-After": "60"})]
    )
    with pytest.raises(hithink.SourceError, match=reason) as error:
        hithink.request("/api/test", {})
    assert "secret" not in str(error.value)
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.args[0] == hithink.BASE + "/api/test"


def test_429_cooldown_is_read_from_disk_and_key_scoped(monkeypatch):
    session = transport(monkeypatch, [response(code=4001, headers={"Retry-After": "120"})])
    with pytest.raises(hithink.SourceError):
        hithink.request("/api/one", {})
    # A new process has no memory of the failed call: the file alone must block it.
    script = "from scutio_data._providers.hithink.client import request,SourceError\ntry: request('/api/two', {})\nexcept SourceError as e: print(e)"
    env = dict(os.environ, PYTHONPATH=str(Path(market.__file__).parents[1]))
    child = subprocess.run(
        [__import__("sys").executable, "-B", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert "cooldown" in child.stdout
    assert session.get.call_count == 1
    monkeypatch.setenv(data_sources.KEY_NAME, "new-test-key")
    session.get.side_effect = [response({"item": []})]
    assert hithink.request("/api/one", {})["data"]["item"] == []


def test_permission_cooldown_is_capability_local(monkeypatch):
    session = transport(monkeypatch, [response(code=2003), response({"item": []})])
    with pytest.raises(hithink.SourceError):
        hithink.request("/api/one", {})
    assert hithink.request("/api/two", {})["data"]["item"] == []
    with pytest.raises(hithink.SourceError, match="cooldown"):
        hithink.request("/api/one", {})
    assert session.get.call_count == 2


def test_cache_preserves_timestamp_and_separates_adjustments(monkeypatch):
    session = transport(
        monkeypatch, [response({"timestamp": None, "item": []}), response({"item": []})]
    )
    a = hithink.request("/api/test", {"adjust": "none"}, ttl=30)
    assert hithink.request("/api/test", {"adjust": "none"}, ttl=30) == a
    assert a["data_as_of"] is None
    hithink.request("/api/test", {"adjust": "forward"}, ttl=30)
    assert session.get.call_count == 2
    for path in (cache_dir() / "api" / "responses" / "hithink").rglob("*.json"):
        assert "test-credential" not in path.read_text(encoding="utf-8")


def test_transient_retry_limit_and_malformed_schema(monkeypatch):
    session = transport(monkeypatch, [response(code=5001)] * 3)
    with pytest.raises(hithink.SourceError):
        hithink.request("/api/test", {})
    assert session.get.call_count == 2
    session.get.side_effect = [response({"item": {}})]
    with pytest.raises(hithink.SourceError, match="schema"):
        hithink.request("/api/test", {})


def test_batch_fills_only_missing_and_does_not_mix_quote_rows(monkeypatch):
    monkeypatch.setenv(data_sources.KEY_NAME, "test-key")
    premium = {"symbol": "sh600519", "price": 100, "volume": 123, "source": "hithink"}
    monkeypatch.setattr(hithink, "quotes", lambda codes: {"sh600519": premium})
    fallback = Mock(return_value={"sz300750": {"price": 200, "volume": 456}})
    monkeypatch.setattr(quote_source, "tencent_quote", fallback)
    env = market.security_quote(["600519", "300750"])
    fallback.assert_called_once_with(["300750"])
    assert env["quotes"]["sh600519"]["volume"] == 123
    assert env["sources_used"] == ["hithink", "tencent"]
    assert env["attempted_sources"] == ["hithink", "tencent"]
    assert env["ok"] and not env["partial"] and env["fallback_reason"]


def test_explicit_hithink_does_not_fallback(monkeypatch):
    monkeypatch.setattr(hithink, "quotes", Mock(side_effect=hithink.SourceError("authentication")))
    free = Mock(side_effect=AssertionError("unexpected fallback"))
    monkeypatch.setattr(quote_source, "tencent_quote", free)
    assert not market.security_quote("600519", sources=["hithink"])["ok"]
    free.assert_not_called()


def test_bar_parent_identity_and_adjustment_checked(monkeypatch):
    identity = raw("600519_identity")["data"]["item"][0]
    payload = raw("600519_bars")["data"]
    monkeypatch.setattr(hithink, "identity", lambda _: identity)
    request = Mock(
        return_value={
            "source": "hithink",
            "retrieved_at": "original",
            "data_as_of": None,
            "data": payload,
        }
    )
    monkeypatch.setattr(hithink, "request", request)
    rows = hithink.bars("600519", count=5)
    assert len(rows) == 5 and rows[-1]["volume_unit"] == "share"
    assert request.call_args.args[1]["adjust"] == "none"
    with pytest.raises(hithink.SourceError, match="adjustment"):
        hithink.bars("600519", count=5, adjust="qfq")


def test_hithink_dividend_per_share_and_special_events():
    items = hithink_parse.dividends(raw("300750_dividends")["data"]["item"], "300750.SZ")
    events = {row["date"]: row for row in items}
    assert events["2024-04-30"]["bonus_rmb"] == pytest.approx(50.28)
    assert events["2025-01-24"]["dividend_per_share"] == pytest.approx(1.23)
    assert events["2025-01-24"]["transfer_ratio"] is None


def test_3002_dividend_falls_back_but_strict_source_fails(monkeypatch):
    monkeypatch.setenv(data_sources.KEY_NAME, "test-key")
    monkeypatch.setattr(
        hithink, "dividends", Mock(side_effect=hithink.SourceError("data_not_ready"))
    )
    fallback = Mock(return_value={"ok": True, "items": []})
    monkeypatch.setattr(capital_dividends, "_dividend_history_public", fallback)
    assert capital.dividend_history("688981")["fallback_reason"] == {"hithink": "data_not_ready"}
    assert not capital.dividend_history("688981", sources=["hithink"])["ok"]
    assert fallback.call_count == 1


def test_full_statement_does_not_shrink_with_key(monkeypatch):
    monkeypatch.setenv(data_sources.KEY_NAME, "test-key")
    monkeypatch.setattr(
        hithink, "financial_summary", Mock(side_effect=AssertionError("full must not call summary"))
    )
    monkeypatch.setattr(
        fundamentals,
        "_financial_report_a",
        lambda *a, **k: [{"报告期": "2025-12-31", "_line_items": [{"id": "original"}]}],
    )
    result = fundamentals.financial_report("600519", num=1)
    assert result["detail"] == "full" and result["items"][0]["_line_items"][0]["id"] == "original"


def test_financial_summary_preserves_bank_native_scope(monkeypatch):
    payload = raw("601398_llb_all")
    monkeypatch.setattr(hithink, "identity", lambda code: {"thscode": "601398.SH"})
    monkeypatch.setattr(
        hithink,
        "request",
        lambda *a, **k: {
            "data": payload["data"],
            "source": "hithink",
            "retrieved_at": "original",
            "data_as_of": None,
        },
    )
    result = hithink.financial_summary("601398", "llb", 8, "all")
    assert result["field_schema"] == "hithink" and result["amount_unit"] == "CNY"
    row = next(row for row in result["items"] if row["报告期"] == "2025-12-31")
    assert row["pay_dividends_profits_interest_cash"] == 190922000000
    assert "分配股利、利润或偿付利息支付的现金" not in row


def test_summary_failure_returns_labelled_full_superset(monkeypatch):
    monkeypatch.setenv(data_sources.KEY_NAME, "test-key")
    monkeypatch.setattr(
        hithink, "financial_summary", Mock(side_effect=hithink.SourceError("permission"))
    )
    monkeypatch.setattr(
        fundamentals, "_financial_report_a", lambda *a, **k: [{"报告期": "2025-12-31"}]
    )
    result = fundamentals.financial_report("600519", num=1, detail="summary")
    assert result["detail"] == "full" and result["requested_detail"] == "summary"
    assert result["field_schema"] == "source_native" and result["fallback_reason"] == "permission"


def test_akshare_null_amount_and_share_unit(monkeypatch):
    monkeypatch.setattr(
        akshare_source,
        "fetch",
        lambda *a, **k: [
            {"date": "2026-09-09", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 123}
        ],
    )
    row = akshare_market.bars("600519", count=1)[0]
    assert row["amount"] is None and row["volume"] == 123
    assert row["volume_unit"] == "share"
    row = akshare_market.bars("600519", count=1, source="eastmoney")[0]
    assert row["volume"] == 12300


def test_akshare_worker_is_allowlisted_and_has_total_deadline(monkeypatch):
    runner = Mock(side_effect=subprocess.TimeoutExpired("worker", 25))
    monkeypatch.setattr(akshare_source, "managed_run", runner)
    monkeypatch.setenv(data_sources.KEY_NAME, "test-key")
    with pytest.raises(RuntimeError, match="total request timeout"):
        akshare_source.fetch("stock_zh_a_daily", symbol="sh600519")
    assert runner.call_args.kwargs["timeout"] == pytest.approx(60, abs=0.1)
    assert data_sources.KEY_NAME not in runner.call_args.kwargs["env"]
    with pytest.raises(ValueError):
        akshare_source.fetch("eval")


def test_valuation_keeps_separate_quote_and_metric_sources(monkeypatch):
    monkeypatch.setenv(data_sources.KEY_NAME, "test-key")
    monkeypatch.setattr(
        hithink,
        "valuation",
        lambda code: {
            "pe_ttm": 10,
            "pe_mrq": -5,
            "pb_mrq": 2,
            "pb": 2,
            "pb_basis": "MRQ",
            "ps_ttm": 3,
            "pcf_ttm": -4,
            "retrieved_at": "metric-time",
            "data_as_of": 123,
            "source": "hithink",
        },
    )
    monkeypatch.setattr(valuation, "security_quote", Mock(side_effect=AssertionError("must reuse")))
    env = {
        "ok": True,
        "retrieved_at": "quote-time",
        "quotes": {"sh600519": {"symbol": "sh600519", "price": 100, "source": "tencent"}},
    }
    result = valuation.valuation_snapshot("600519", quote_env=env)
    assert result["retrieved_at"] == "metric-time"
    assert result["input_quote_retrieved_at"] == "quote-time"
    assert result["field_sources"]["price"] == "tencent"
    assert result["field_sources"]["pb"] == "hithink"
    assert result["pe_mrq"] == -5 and "pe_static" not in result


def test_tencent_adjustment_is_rejected_before_akshare_can_choose_raw_series(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("ambiguous adjustment adapter must not run")

    monkeypatch.setattr(akshare_source, "fetch", fail)
    for adjust in ("qfq", "hfq"):
        result = market.security_bars("600519", count=1, adjust=adjust, sources=["akshare_tencent"])
        assert not result["ok"] and "adjustment series is not verified" in result["error"]
        assert result["sources_used"] == []


def test_adjusted_free_bars_keep_source_native_basis(monkeypatch):
    def fetch(function, **params):
        assert params["adjust"] == "qfq"
        if function == "stock_zh_a_hist":
            raise RuntimeError("em down")
        return [{"date": "2026-09-08", "open": 9, "close": 9, "high": 9, "low": 9}]

    monkeypatch.setattr(akshare_source, "fetch", fetch)
    result = market.security_bars("600519", count=1, adjust="qfq")
    assert result["ok"] and result["source"] == "akshare_sina"
    assert result["adjustment_basis"]["cross_source_equivalent"] is False and result["warning"]


@pytest.mark.parametrize("separate_homes", [False, True])
def test_parallel_settings_update_preserves_public_mode(tmp_path, separate_homes):
    import time

    script = r"""
import sys, time
from pathlib import Path
from scutio_data._runtime import config
marker, release = map(Path, sys.argv[1:3])
original = config.read_json
def paused_read(path):
    value = original(path)
    marker.write_text('read')
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() > deadline:
            raise RuntimeError('test gate timed out')
        time.sleep(0.01)
    return value
config.read_json = paused_read
config.set_setting('hint_seen', True)
"""
    contender = r"""
import sys
from pathlib import Path
from scutio_data._runtime import config
original = config.file_lock
config.file_lock = lambda path: original(path, timeout_seconds=0.1)
try:
    config.set_setting('mode', 'public')
except TimeoutError:
    Path(sys.argv[1]).write_text('blocked')
else:
    Path(sys.argv[1]).write_text('not-blocked')
    raise RuntimeError('settings writers did not share a lock')
config.file_lock = original
config.set_setting('mode', 'public')
"""
    marker, release = tmp_path / "read", tmp_path / "release"
    contention = tmp_path / "contention"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[2] / "skills/scutio/scripts"))
    second_env = dict(env)
    if separate_homes:
        second_env["SCUTIO_HOME"] = str(tmp_path / "other-home")
    # Keep the actual configuration shared even when the runtime homes differ.
    env["SCUTIO_CONFIG_DIR"] = second_env["SCUTIO_CONFIG_DIR"] = str(data_sources.home())
    first = subprocess.Popen([sys.executable, "-c", script, str(marker), str(release)], env=env)
    second = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists():
            assert first.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        second = subprocess.Popen(
            [
                sys.executable,
                "-c",
                contender,
                str(contention),
            ],
            env=second_env,
        )
        deadline = time.monotonic() + 5
        while not contention.exists():
            assert second.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        assert contention.read_text(encoding="utf-8") == "blocked"
        release.write_text("continue")
        assert first.wait(timeout=5) == second.wait(timeout=5) == 0
        assert data_sources.settings() == {"hint_seen": True, "mode": "public"}
        assert data_sources.mode() == "public"
        assert (data_sources.home() / "settings.lock").is_file()
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()


@pytest.mark.parametrize(
    "failures,reason",
    [
        (("proxy", "connection"), "upstream_connection_error"),
        (("proxy", "business"), "transient"),
        (("connection", "proxy"), "proxy_error"),
    ],
)
def test_hithink_route_switch_and_retry_share_two_physical_attempts(monkeypatch, failures, reason):
    import itertools

    import requests
    from scutio_data._runtime.http import Session

    monkeypatch.setenv(data_sources.KEY_NAME, "test-credential")
    events = itertools.cycle(failures)
    calls = []

    def transfer(self, method, url, *, network_trust_env, **kwargs):
        calls.append(network_trust_env)
        failure = next(events)
        if failure == "business":
            return response(code=5001)
        error = (
            requests.exceptions.ProxyError
            if failure == "proxy"
            else requests.exceptions.ConnectionError
        )
        raise error("sensitive network details")

    monkeypatch.setattr(Session, "_transport_request", transfer)
    with pytest.raises(hithink.SourceError, match=reason):
        hithink.request("/api/test", {})
    assert len(calls) == 2


def test_hithink_transient_retry_can_return_success(monkeypatch):
    import requests
    from scutio_data._runtime.http import Session

    monkeypatch.setenv(data_sources.KEY_NAME, "test-credential")
    calls = []

    def transfer(self, method, url, *, network_trust_env, **kwargs):
        calls.append(network_trust_env)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("private network details")
        return response({"item": []})

    monkeypatch.setattr(Session, "_transport_request", transfer)
    assert hithink.request("/api/test", {})["data"]["item"] == []
    assert calls == [True, True]
