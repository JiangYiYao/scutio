"""Offline contracts for bounded quote requests and reusable roster identities."""

from unittest.mock import Mock

import pytest
from scutio_data import market
from scutio_data._providers import quotes
from scutio_data._providers.hithink import client as hithink
from scutio_data._providers.quote_parse import parse_sina_quote_raw, parse_tencent_quote_raw
from scutio_data._runtime.timeouts import RequestCancelled, RequestTimeout


def roster(count=205):
    return [
        {
            "symbol": "sh%s" % (600000 + index),
            "code": str(600000 + index),
            "exchange": "sh",
            "asset_type": "a-share",
            "name": "Company %s" % index,
        }
        for index in range(count)
    ]


def snapshot(params):
    return {
        "source": "hithink",
        "retrieved_at": "2026-09-22T01:00:00+00:00",
        "data_as_of": None,
        "data": {
            "item": [
                {"thscode": code, "last_price": 10, "volume": 100}
                for code in params["thscodes"].split(",")
            ]
        },
    }


@pytest.mark.parametrize("provider", ["tencent", "sina"])
@pytest.mark.parametrize("failed_batch", [0, 1, 2])
def test_quote_urls_are_bounded_and_fallback_fetches_only_failed_batch(
    monkeypatch, provider, failed_batch
):
    codes = [row["symbol"] for row in roster()]
    calls = []

    def transfer(url, **kwargs):
        symbols = url.split("=", 1)[1].split(",")
        calls.append(symbols)
        if len(calls) - 1 == failed_batch:
            raise RuntimeError("batch unavailable")
        return symbols

    monkeypatch.setattr(quotes, "_http_get_bytes", transfer)
    monkeypatch.setattr(
        quotes,
        "parse_%s_quote_raw" % provider,
        lambda symbols: {
            symbol: {"symbol": symbol, "code": symbol[2:], "price": 10} for symbol in symbols
        },
    )
    fallback = Mock(
        side_effect=lambda missing: {symbol: {"symbol": symbol, "price": 20} for symbol in missing}
    )
    monkeypatch.setattr(quotes, "eastmoney_quote", fallback)
    result = market.security_quote(codes, sources=[provider, "eastmoney"])

    assert [len(batch) for batch in calls] == [100, 100, 5]
    fallback.assert_called_once_with(calls[failed_batch])
    assert result["ok"] and not result["partial"]
    assert result["missing"] == [] and result["returned_count"] == len(codes)
    for code in codes:
        expected_source = "eastmoney" if code in calls[failed_batch] else provider
        assert result["quotes"][code]["source"] == expected_source


@pytest.mark.parametrize("provider", ["tencent", "sina"])
def test_quote_batch_timeout_keeps_completed_rows_and_stops(monkeypatch, provider):
    codes = [row["symbol"] for row in roster()]
    transfer = Mock(side_effect=[codes[:100], RequestTimeout("response")])
    monkeypatch.setattr(quotes, "_http_get_bytes", transfer)
    monkeypatch.setattr(
        quotes,
        "parse_%s_quote_raw" % provider,
        lambda symbols: {
            symbol: {"symbol": symbol, "code": symbol[2:], "price": 10} for symbol in symbols
        },
    )
    result = getattr(quotes, provider + "_quote")(codes)
    assert len([key for key in result if key.startswith("sh")]) == 100
    assert transfer.call_count == 2


@pytest.mark.parametrize("provider", ["tencent", "sina"])
def test_all_quote_batches_failing_is_a_source_failure(monkeypatch, provider):
    transfer = Mock(side_effect=RuntimeError("batch unavailable"))
    monkeypatch.setattr(quotes, "_http_get_bytes", transfer)
    with pytest.raises(RuntimeError, match="batch unavailable"):
        getattr(quotes, provider + "_quote")([row["symbol"] for row in roster()])
    assert transfer.call_count == 3


def test_hithink_reuses_roster_and_preserves_other_snapshot_batches(monkeypatch):
    records = roster()
    identity = Mock(side_effect=AssertionError("verified roster must avoid individual lookups"))
    monkeypatch.setattr(hithink, "identity", identity)
    batches = []

    def request(path, params, **kwargs):
        assert path == "/api/a-share/prices/snapshot"
        batches.append(params["thscodes"].split(","))
        if len(batches) == 2:
            raise hithink.SourceError("snapshot unavailable")
        return snapshot(params)

    monkeypatch.setattr(hithink, "request", request)
    result = hithink.quotes([row["symbol"] for row in records], identity_records=records)
    assert [len(batch) for batch in batches] == [100, 100, 5]
    assert len(result) == 105
    assert result["sh600000"]["name"] == "Company 0"
    assert "sh600100" not in result and "sh600204" in result
    identity.assert_not_called()


def test_hithink_missing_roster_identity_still_resolves_exact_identity(monkeypatch):
    records = roster(2)
    identity = Mock(return_value={"thscode": "600001.SH", "name": "Looked up"})
    monkeypatch.setattr(hithink, "identity", identity)
    monkeypatch.setattr(hithink, "request", lambda path, params, **kwargs: snapshot(params))
    result = hithink.quotes(["sh600000", "sh600001"], identity_records=records[:1])
    identity.assert_called_once_with("sh600001")
    assert result["sh600001"]["name"] == "Looked up"


def test_hithink_finishes_each_quote_batch_before_next_identity_batch(monkeypatch):
    records = roster()
    calls = []

    def identity(code):
        calls.append(("identity", code))
        if code == "sh600100":
            raise RequestTimeout("response")
        return {"thscode": code[2:] + ".SH", "name": code}

    def request(path, params, **kwargs):
        calls.append(("snapshot", params))
        return snapshot(params)

    monkeypatch.setattr(hithink, "identity", identity)
    monkeypatch.setattr(hithink, "request", request)
    result = hithink.quotes([row["symbol"] for row in records])
    assert len(result) == 100
    assert calls[100][0] == "snapshot"
    assert calls[101] == ("identity", "sh600100")
    assert len(calls) == 102


def test_hithink_roster_does_not_override_conflicting_source_identity(monkeypatch):
    records = roster(3)
    value = snapshot({"thscodes": "600000.SH,600001.SH,600002.SH,600003.SH"})
    value["data"]["item"][0]["exchange"] = "sz"
    value["data"]["item"][1]["code"] = "600002"
    monkeypatch.setattr(hithink, "request", lambda *args, **kwargs: value)
    monkeypatch.setattr(hithink, "identity", Mock(side_effect=AssertionError("no lookup")))
    result = hithink.quotes([row["symbol"] for row in records], identity_records=records)
    assert set(result) == {"sh600002"}


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "600000"},
        {"symbol": "sh000001", "code": "000001"},
        {"symbol": "sh510300", "code": "510300"},
        {"symbol": "hk00700", "code": "00700", "exchange": "hk"},
        {"symbol": "usAAPL", "code": "AAPL", "exchange": "us"},
        {"asset_type": "fund"},
        {"exchange": "sz"},
        {"exchange": None},
        {"code": "600001"},
        {"thscode": "600001.SH"},
        {"currency": "USD"},
    ],
)
def test_invalid_roster_identity_fails_before_network(monkeypatch, change):
    forbidden = Mock(side_effect=AssertionError("invalid roster must fail before fetching"))
    monkeypatch.setattr(hithink, "quotes", forbidden)
    record = dict(roster(1)[0], **change)
    result = market.security_quote(["sh600000"], sources=["hithink"], identity_records=[record])
    assert not result["ok"] and result["error_code"] == "invalid_arguments"
    assert result["quotes"] == {} and result["returned_count"] == 0
    assert result["attempted_sources"] == []
    forbidden.assert_not_called()


def test_roster_duplicate_conflicts_are_rejected_but_identical_records_are_reusable():
    record = roster(1)[0]
    assert len(hithink._quote_identities([record, record.copy()])) == 1
    with pytest.raises(ValueError, match="duplicate"):
        hithink._quote_identities([record, dict(record, name="Conflicting name")])
    with pytest.raises(ValueError, match="list"):
        hithink._quote_identities({record["symbol"]: record})


def test_facade_forwards_roster_and_fallback_only_fills_missing_snapshot_rows(monkeypatch):
    records = roster(2)
    monkeypatch.setattr(hithink, "preferred", lambda code: True)
    monkeypatch.setattr(hithink, "identity", Mock(side_effect=AssertionError("no lookup")))
    monkeypatch.setattr(
        hithink, "request", lambda *args, **kwargs: snapshot({"thscodes": "600000.SH"})
    )
    fallback = Mock(return_value={"sh600001": {"symbol": "sh600001", "price": 20}})
    monkeypatch.setattr(quotes, "tencent_quote", fallback)
    result = market.security_quote(["sh600000", "sh600001"], identity_records=records)
    fallback.assert_called_once_with(["sh600001"])
    assert result["quotes"]["sh600000"]["source"] == "hithink"
    assert result["quotes"]["sh600001"]["source"] == "tencent"
    assert result["returned_count"] == 2 and not result["missing"]


@pytest.mark.parametrize("provider", ["tencent", "sina", "hithink"])
def test_quote_batching_does_not_swallow_cancellation(monkeypatch, provider):
    cancelled = Mock(side_effect=RequestCancelled("test stop"))

    def call():
        if provider == "hithink":
            monkeypatch.setattr(hithink, "request", cancelled)
            return hithink.quotes(["sh600000"], identity_records=roster(1))
        monkeypatch.setattr(quotes, "_http_get_bytes", cancelled)
        return getattr(quotes, provider + "_quote")(["sh600000"])

    with pytest.raises(RequestCancelled):
        call()


@pytest.mark.parametrize("missing", ["", "-", "nan", "inf", "invalid"])
def test_tencent_missing_numeric_quote_fields_remain_unknown(missing):
    fields = [missing] * 60
    fields[1], fields[2] = "Company", "600000"
    row = parse_tencent_quote_raw('v_sh600000="' + "~".join(fields) + '";')["sh600000"]
    for field in (
        "price",
        "last_close",
        "open",
        "high",
        "low",
        "change_amt",
        "change_pct",
        "amplitude_pct",
        "amount",
        "amount_wan",
        "volume",
    ):
        assert row[field] is None, field


def test_tencent_actual_zero_change_and_activity_are_preserved():
    fields = ["0"] * 60
    fields[1], fields[2], fields[3], fields[4] = "Company", "600000", "10", "10"
    row = parse_tencent_quote_raw('v_sh600000="' + "~".join(fields) + '";')["sh600000"]
    for field in ("change_amt", "change_pct", "amplitude_pct", "amount", "amount_wan", "volume"):
        assert row[field] == 0, field


@pytest.mark.parametrize("missing", ["", "-", "nan", "inf", "invalid"])
def test_sina_missing_inputs_do_not_invent_changes_or_activity(missing):
    fields = ["Company", missing, missing, "10", missing, missing, "0", "0", missing, missing]
    row = parse_sina_quote_raw('var hq_str_sh600000="' + ",".join(fields) + '";')["sh600000"]
    for field in (
        "last_close",
        "open",
        "high",
        "low",
        "change_amt",
        "change_pct",
        "amplitude_pct",
        "amount",
        "amount_wan",
        "volume",
    ):
        assert row[field] is None, field


def test_sina_truncated_quote_keeps_missing_fields_and_real_zeros_separate():
    row = parse_sina_quote_raw('var hq_str_sh600000="Company,10,10,10";')["sh600000"]
    assert row["amount"] is None and row["amplitude_pct"] is None
    assert row["high"] is None and row["low"] is None
    assert row["change_amt"] == row["change_pct"] == 0
    row = parse_sina_quote_raw('var hq_str_sh600000="Company,10,10,10,10,10,0,0,0,0";')["sh600000"]
    assert row["amount"] == row["amount_wan"] == row["volume"] == row["amplitude_pct"] == 0


def test_eastmoney_timeout_keeps_completed_rows_and_stops(monkeypatch):
    fetch = Mock(
        side_effect=[
            {"symbol": "hk00700", "code": "00700", "price": 10},
            RequestTimeout("response"),
        ]
    )
    monkeypatch.setattr(quotes, "_eastmoney_quote_one", fetch)
    result = quotes.eastmoney_quote(["hk00700", "hk00941", "hk09988"])
    assert "hk00700" in result
    assert fetch.call_count == 2
