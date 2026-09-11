"""Selected evidence must not become a hidden full-report or price gate."""

from copy import deepcopy
from unittest.mock import Mock

import _collection_modules as modules
import _research_collection as collection
import collect_research_base as cli
import pytest


@pytest.fixture
def endpoints(monkeypatch):
    specs = {
        "profile": (modules.fundamentals, "stock_info"),
        "quote": (modules.market, "security_quote"),
        "financial": (modules.fundamentals, "financial_report"),
        "filings": (modules.announcements, "periodic_reports"),
        "reports": (modules.research, "stock_reports"),
        "news": (modules.feeds, "stock_news"),
        "consensus": (modules.research, "consensus_forecast"),
        "valuation": (modules.valuation, "valuation_snapshot"),
    }
    mocks = {}
    for name, (module, function) in specs.items():
        mocks[name] = Mock(return_value={"ok": True, "source": name, "items": []})
        monkeypatch.setattr(module, function, mocks[name])
    return mocks


@pytest.mark.parametrize("symbol", ["sh600519", "hk00700", "usAAPL"])
def test_income_only_preserves_identity_and_does_not_fetch_price(endpoints, symbol):
    result = collection.collect(symbol, question="利润来自哪里？", modules=["income"])
    endpoints["financial"].assert_called_once_with(symbol, "lrb", 4, period="annual")
    assert set(result["modules"]) == {"income"}
    assert result["ok"] and not result["partial"]
    for name, endpoint in endpoints.items():
        if name != "financial":
            endpoint.assert_not_called()


def test_price_failure_leaves_financial_evidence_usable(endpoints):
    endpoints["quote"].side_effect = TimeoutError("quote unavailable")
    result = collection.collect("600519", question="经营是否改善？", modules=["quote", "income"])
    assert result["ok"] and result["partial"]
    assert set(result["errors"]) == {"quote"}
    assert result["modules"]["income"]["ok"]
    assert "decision_readiness" not in result


def test_truthy_failure_envelope_is_not_success_and_empty_success_is(endpoints):
    endpoints["news"].return_value = {"ok": False, "error": "unavailable"}
    failed = collection.collect("600519", question="有新消息吗？", modules=["news"])
    assert not failed["ok"] and not failed["partial"]
    endpoints["news"].return_value = {"ok": True, "items": [], "source": "fixture"}
    empty = collection.collect("600519", question="有新消息吗？", modules=["news"])
    assert empty["ok"] and not empty["errors"]


@pytest.mark.parametrize(
    "change",
    [
        {"code": "AAPL"},
        {"question": ""},
        {"modules": []},
        {"modules": ["everything"]},
        {"period": "fake"},
        {"depth": "automatic"},
    ],
)
def test_invalid_request_fails_before_io(endpoints, change):
    args = dict(code="600519", question="收入变化？", modules=["income"])
    args.update(change)
    with pytest.raises(ValueError):
        collection.collect(**args)
    assert all(not endpoint.called for endpoint in endpoints.values())


def test_valuation_uses_requested_quote_even_on_failure(endpoints):
    quote = {"ok": False, "error": "unavailable"}
    endpoints["quote"].return_value = quote
    collection.collect("600519", question="价格口径？", modules=["valuation", "quote"])
    endpoints["quote"].assert_called_once()
    endpoints["valuation"].assert_called_once_with("sh600519", quote_env=quote)


def test_reuse_preserves_time_and_refetches_changed_period(endpoints):
    old = collection.collect("600519", question="年度收入？", modules=["income"])
    old["observations"]["income"]["collected_at"] = "2025-01-01T00:00:00+00:00"
    frozen = deepcopy(old)
    result = collection.collect(
        "600519", question="旧材料的收入口径？", modules=["income"], reuse=old
    )
    assert result["observations"]["income"]["collected_at"] == "2025-01-01T00:00:00+00:00"
    assert result["observations"]["income"]["reused"]
    assert old == frozen
    endpoints["financial"].assert_called_once()
    changed = collection.collect(
        "600519", question="季度收入？", modules=["income"], period="quarter", reuse=old
    )
    assert not changed["observations"]["income"]["reused"]
    assert endpoints["financial"].call_count == 2
    with pytest.raises(ValueError, match="does not match"):
        collection.collect("hk00700", question="收入？", modules=["income"], reuse=old)


@pytest.mark.parametrize(
    "stamp", [None, "yesterday", "2025-01-01T00:00:00", "2999-01-01T00:00:00+00:00"]
)
def test_unusable_timestamp_is_not_reused(endpoints, stamp):
    old = collection.collect("600519", question="收入？", modules=["income"])
    old["observations"]["income"]["collected_at"] = stamp
    collection.collect("600519", question="收入？", modules=["income"], reuse=old)
    assert endpoints["financial"].call_count == 2


@pytest.mark.parametrize("same_timestamp", [False, True])
def test_refreshing_quote_also_refreshes_derived_valuation(endpoints, monkeypatch, same_timestamp):
    if same_timestamp:
        monkeypatch.setattr(collection, "_timestamp", lambda: "2025-01-01T00:00:00+00:00")
    old = collection.collect("600519", question="估值？", modules=["quote", "valuation"])
    old["modules"]["quote"] = {"ok": False, "error": "not reusable"}
    result = collection.collect(
        "600519", question="估值？", modules=["valuation", "quote"], reuse=old
    )
    assert endpoints["valuation"].call_count == 2
    assert not result["observations"]["valuation"]["reused"]


def test_valuation_only_can_use_previous_quote_without_refreshing_its_time(endpoints):
    old = collection.collect("600519", question="价格？", modules=["quote"])
    old["observations"]["quote"]["collected_at"] = "2025-01-01T00:00:00+00:00"
    result = collection.collect("600519", question="当时估值？", modules=["valuation"], reuse=old)
    endpoints["quote"].assert_called_once()
    endpoints["valuation"].assert_called_once_with("sh600519", quote_env=old["modules"]["quote"])
    assert (
        result["observations"]["valuation"]["input_collected_at"]
        == old["observations"]["quote"]["collected_at"]
    )
    assert (
        result["observations"]["valuation"]["collected_at"]
        != old["observations"]["quote"]["collected_at"]
    )


def test_unsupported_domains_do_not_call_a_share_sources(endpoints):
    result = collection.collect("usAAPL", question="卖方预期？", modules=["reports", "consensus"])
    assert not result["ok"]
    assert set(result["errors"]) == {"reports", "consensus"}
    assert all(not endpoint.called for endpoint in endpoints.values())


def test_cli_requires_question_and_modules_before_collection(endpoints):
    with pytest.raises(SystemExit) as exc:
        cli.main(["600519"])
    assert exc.value.code == 2
    assert all(not endpoint.called for endpoint in endpoints.values())


def test_macro_only_needs_no_security_and_calls_only_selected_series(endpoints, monkeypatch):
    fetch = Mock(return_value={"ok": True, "items": [], "partial": True, "stale": True})
    monkeypatch.setattr(modules.macro, "macro_series", fetch)
    result = collection.collect(question="通胀变化？", modules=["macro:cpi_yoy", "macro:cpi_yoy"])
    fetch.assert_called_once_with(name="cpi_yoy", limit=12)
    assert result["code"] is None and result["market"] is None
    assert set(result["modules"]) == {"macro:cpi_yoy"}
    assert result["partial"] and result["modules"]["macro:cpi_yoy"]["stale"]
    assert all(not endpoint.called for endpoint in endpoints.values())


def test_global_rates_and_fx_are_independent_modules(endpoints, monkeypatch):
    rates = Mock(return_value={"ok": True})
    fx = Mock(return_value={"ok": False, "error": "unavailable"})
    monkeypatch.setattr(modules.macro, "rates_snapshot", rates)
    monkeypatch.setattr(modules.macro, "fx_usdcny", fx)
    result = collection.collect(question="利率与汇率？", modules=["rates", "fx"])
    assert result["ok"] and result["partial"] and result["errors"] == {"fx": "unavailable"}
    rates.assert_called_once_with()
    fx.assert_called_once_with()
    assert all(not endpoint.called for endpoint in endpoints.values())


@pytest.mark.parametrize(
    "args",
    [
        {"modules": ["macro:not_real"]},
        {"modules": ["income"], "code": None},
        {"modules": ["peers"], "peers": []},
        {"modules": ["peers"], "peers": [{"code": "600519", "relation": "同业", "basis": "test"}]},
        {"modules": ["peers"], "peers": [{"code": "hk00700"}]},
    ],
)
def test_module_requirements_are_validated_before_network(endpoints, args):
    options = {"code": "600519", "question": "问题", **args}
    with pytest.raises(ValueError):
        collection.collect(**options)
    assert all(not endpoint.called for endpoint in endpoints.values())


def test_peer_quotes_preserve_mapping_and_source_time(endpoints):
    mapping = [
        {"code": "hk00700", "relation": "同业", "basis": "同类业务", "source_ref": "company-report"}
    ]
    endpoints["quote"].return_value = {
        "ok": True,
        "quotes": {"hk00700": {"data_as_of": "2025-01-01", "price": 10}},
    }
    result = collection.collect("600519", question="同行资料？", modules=["peers"], peers=mapping)
    endpoints["quote"].assert_called_once_with(["hk00700"])
    peers = result["modules"]["peers"]
    assert peers["peer_mapping"] == mapping
    assert peers["quotes"]["hk00700"]["data_as_of"] == "2025-01-01"
    changed = [{**mapping[0], "basis": "另一份材料"}]
    collection.collect(
        "600519", question="同行资料？", modules=["peers"], peers=changed, reuse=result
    )
    assert endpoints["quote"].call_count == 2


def test_revisions_reuse_selected_reports_without_duplicate_fetch(endpoints, monkeypatch):
    revisions = Mock(return_value={"ok": True, "items": []})
    monkeypatch.setattr(modules.research, "consensus_revisions", revisions)
    result = collection.collect(
        "600519", question="预期如何变化？", modules=["revisions", "reports"]
    )
    endpoints["reports"].assert_called_once_with("sh600519", max_pages=1)
    revisions.assert_called_once_with("sh600519", max_pages=1, reports=result["modules"]["reports"])
    assert (
        result["observations"]["revisions"]["input_collected_at"]
        == result["observations"]["reports"]["collected_at"]
    )
    old = deepcopy(result)
    old["modules"]["reports"] = {"ok": False, "error": "expired"}
    new = collection.collect(
        "600519", question="预期变化？", modules=["revisions", "reports"], reuse=old
    )
    assert not new["observations"]["revisions"]["reused"]
    assert revisions.call_count == 2


def test_explicit_daily_bars_do_not_expand_financials(endpoints, monkeypatch):
    bars = Mock(return_value={"ok": True, "bars": []})
    monkeypatch.setattr(modules.market, "security_bars", bars)
    result = collection.collect("usAAPL", question="日线变化？", modules=["bars"], depth="full")
    bars.assert_called_once_with("usAAPL", frequency="D", count=160, adjust="none")
    assert set(result["modules"]) == {"bars"}
    assert all(not endpoint.called for endpoint in endpoints.values())


def test_unified_cli_writes_collection_without_run_or_role_files(endpoints, tmp_path):
    import json

    output = tmp_path / "artifacts" / "evidence.json"
    assert (
        cli.main(["600519", "--question", "收入？", "--modules", "income", "-o", str(output)]) == 0
    )
    assert set(json.loads(output.read_text())["modules"]) == {"income"}
    assert {path.name for path in output.parent.iterdir()} == {"evidence.json"}
