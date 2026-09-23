"""Public screen_market contracts with independent, offline provider envelopes."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from scutio_data import fundamentals, market, screening, universe
from scutio_data._providers.akshare.financial_snapshot import FIELD_CATALOG


@pytest.fixture
def world(monkeypatch):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    current = date(now.year - 1, 6, 30)
    previous = current.replace(year=current.year - 1)
    state = SimpleNamespace(
        now=now,
        current=current.isoformat(),
        previous=previous.isoformat(),
        current_retrieved=now.timestamp() - 60,
        prior_retrieved=now.timestamp() - 120,
        symbols=["sh600001", "sz000001", "bj920001"],
        quotes={},
        reports={},
        universe_rows=[],
        universe_complete=True,
        calls={"universe": [], "quotes": [], "financials": []},
    )

    def populate(symbols):
        state.symbols = list(symbols)
        state.universe_rows = [
            {
                "symbol": symbol,
                "code": symbol[2:],
                "exchange": symbol[:2],
                "name": "fixture-" + symbol,
                "board": "fixture-board",
                "listed_date": date(now.year - 5, 1, 1).isoformat(),
                "asset_type": "stock",
                "source": "fixture_listing_directory",
            }
            for symbol in symbols
        ]
        state.quotes = {
            symbol: {
                "symbol": symbol,
                "code": symbol[2:],
                "exchange": symbol[:2],
                "currency": "CNY",
                "source": "tencent",
                "data_as_of": now.isoformat(),
                "retrieved_at": now.isoformat(),
                "price": 20,
                "pe_ttm": 10,
                "mcap_yi": 100,
            }
            for symbol in symbols
        }
        state.reports = {
            period.isoformat(): [
                {
                    "symbol": symbol,
                    "code": symbol[2:],
                    "exchange": symbol[:2],
                    "source": "fixture_financial_snapshot",
                    "currency": "CNY",
                    "report_date": period.isoformat(),
                    "report_date_basis": "request_filter",
                    "period_type": "ytd",
                    "disclosed_at": (period + timedelta(days=45)).isoformat(),
                    "disclosed_at_basis": "provider_update_date_not_first_disclosure",
                    "time_basis": "retrospective_current_materials",
                    "field_status": {},
                    "revenue": revenue,
                    "net_profit": 20,
                    "revenue_yoy": 100,
                    "roe": 10,
                    "industry": "fixture_industry",
                }
                for symbol in symbols
            ]
            for period, revenue in ((current, 200), (previous, 100))
        }

    state.populate = populate
    populate(state.symbols)

    def stock_universe(market="a"):
        state.calls["universe"].append(market)
        return {
            "ok": True,
            "error": None,
            "source": "fixture_listing_directory",
            "items": deepcopy(state.universe_rows),
            "universe_type": "exchange_listing_directory",
            "as_of": now.isoformat(),
            "is_complete": state.universe_complete,
            "partial": state.universe_complete is not True,
            "coverage": {"exchanges": ["sh", "sz", "bj"]},
        }

    def quote(codes, **kwargs):
        state.calls["quotes"].append((list(codes), kwargs))
        return {
            "ok": True,
            "error": None,
            "quotes": {
                code: deepcopy(state.quotes[code]) for code in codes if code in state.quotes
            },
            "errors": {},
        }

    def financial(period, *, codes=None):
        state.calls["financials"].append((period, list(codes) if codes is not None else None))
        return {
            "ok": True,
            "error": None,
            "source": "fixture_financial_snapshot",
            "report_date": period,
            "period_type": "ytd",
            "currency": "CNY",
            "time_basis": "retrospective_current_materials",
            "field_catalog": deepcopy(FIELD_CATALOG),
            "snapshot_retrieved_at": (
                state.current_retrieved if period == state.current else state.prior_retrieved
            ),
            "snapshot_cached": True,
            "snapshot_ttl_seconds": 3600,
            "items": [
                deepcopy(row)
                for row in state.reports.get(period, [])
                if codes is None or row["symbol"] in codes
            ],
            "partial": True,
            "complete": False,
        }

    monkeypatch.setattr(universe, "stock_universe", stock_universe)
    monkeypatch.setattr(market, "security_quote", quote)
    monkeypatch.setattr(fundamentals, "financial_snapshot", financial)
    return state


def _financial_screen(world, field="revenue", **kwargs):
    return screening.screen_market(
        {field: {"min": 50}}, codes=["sh600001"], report_date=world.current, **kwargs
    )


def _assert_unknown(out, field):
    assert out["ok"]
    assert out["matched_count"] == 0 and out["excluded_count"] == 0
    assert out["missing_count"] == 1 and out["partial"]
    assert out["coverage"]["screen_complete"] is False
    assert out["missing_rows"][0]["symbol"] == "sh600001"
    assert field in out["missing_rows"][0]["fields"]


def _timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(value).timestamp()


def test_feature_catalog_is_discoverable_and_does_not_fetch(world):
    out = screening.feature_catalog()
    fields = {row["name"]: row for row in out["items"]}
    assert out["ok"] and not any(world.calls.values())
    assert set(FIELD_CATALOG) <= set(fields)
    assert fields["revenue"]["requires_report_date"]
    assert not fields["price"]["requires_report_date"]
    assert fields["revenue_growth_pct"]["prior_year"]
    assert fields["revenue_yoy"]["basis"] == "provider_reported_base_unknown"
    fields["price"]["label"] = "mutated"
    assert "mutated" not in str(screening.feature_catalog())


def test_unsupported_feature_fails_before_any_provider(world):
    out = screening.screen_market({"invented_metric": {"min": 1}})
    assert not out["ok"] and not any(world.calls.values())


@pytest.mark.parametrize("field", ["name", "listed_date"])
def test_nonnumeric_market_sort_is_rejected_before_fetch(world, field):
    out = screening.screen_market({"exchange": "sh"}, sort_by=field)
    assert not out["ok"] and not any(world.calls.values())


@pytest.mark.parametrize(
    "field,period", [("revenue", "2009-12-31"), ("revenue_growth_pct", "2010-12-31")]
)
def test_unsupported_financial_history_fails_before_quote_fetch(world, field, period):
    out = screening.screen_market({"price": {"gt": 0}, field: {"gt": 0}}, report_date=period)
    assert not out["ok"] and not any(world.calls.values())


def test_financial_period_is_required_before_any_provider(world):
    out = screening.screen_market({"revenue": {"min": 1}})
    assert not out["ok"] and not any(world.calls.values())


@pytest.mark.parametrize("period_type", ["not_quarter_end", "future"])
def test_bad_financial_period_fails_before_any_provider(world, period_type):
    period = (
        date(world.now.year - 1, 6, 29)
        if period_type == "not_quarter_end"
        else date(world.now.year + 1, 12, 31)
    )
    out = screening.screen_market({"revenue": {"min": 1}}, report_date=period.isoformat())
    assert not out["ok"] and not any(world.calls.values())


def test_quotes_are_batched_without_per_security_calls(world):
    symbols = [f"sh{600000 + index:06d}" for index in range(205)]
    world.populate(symbols)
    out = screening.screen_market({"price": {"min": 10}}, limit=None)
    calls = world.calls["quotes"]
    assert out["ok"] and out["matched_count"] == 205
    assert sorted(len(codes) for codes, _ in calls) == [5, 100, 100]
    assert {code for codes, _ in calls for code in codes} == set(symbols)
    assert not world.calls["financials"]


def test_missing_quotes_remain_unknown_not_negative_matches(world):
    world.quotes["sz000001"]["price"] = 5
    world.quotes.pop("bj920001")
    out = screening.screen_market({"price": {"min": 10}})
    assert out["ok"] and out["matched_count"] == 1
    assert out["excluded_count"] == 1 and out["missing_count"] == 1
    assert out["partial"] and out["coverage"]["screen_complete"] is False
    assert out["missing_rows"][0]["symbol"] == "bj920001"


def test_financials_only_fetch_rows_not_already_proven_false(world):
    world.quotes["sh600001"]["price"] = 1
    world.quotes.pop("bj920001")
    out = screening.screen_market(
        {"price": {"min": 10}, "revenue": {"min": 50}}, report_date=world.current
    )
    assert out["matched_count"] == 1 and out["missing_count"] == 1
    assert out["excluded_count"] == 1
    assert len(world.calls["financials"]) == 1
    assert set(world.calls["financials"][0][1]) == {"sz000001", "bj920001"}


def test_proven_false_universe_condition_avoids_financial_requests(world):
    out = screening.screen_market(
        {"exchange": "hk", "revenue": {"min": 50}}, report_date=world.current
    )
    assert out["ok"] and out["excluded_count"] == 3 and out["missing_count"] == 0
    assert not world.calls["quotes"] and not world.calls["financials"]


def test_growth_uses_same_quarter_previous_year_and_positive_base(world):
    out = _financial_screen(world, "revenue_growth_pct")
    assert out["matched_count"] == 1
    assert out["items"][0]["revenue_growth_pct"] == pytest.approx(100)
    assert sorted(period for period, _ in world.calls["financials"]) == [
        world.previous,
        world.current,
    ]


@pytest.mark.parametrize("base", [0, -100])
def test_nonpositive_growth_base_is_unknown(world, base):
    world.reports[world.previous][0]["revenue"] = base
    out = _financial_screen(world, "revenue_growth_pct")
    _assert_unknown(out, "revenue_growth_pct")
    assert out["missing_rows"][0]["reasons"]["revenue_growth_pct"] == "nonpositive_base"


@pytest.mark.parametrize("issue", ["future", "missing", "conflicting_rows", "before_report_date"])
def test_unusable_financial_update_date_never_proves_match(world, issue):
    row = world.reports[world.current][0]
    if issue == "future":
        row["disclosed_at"] = (world.now.date() + timedelta(days=1)).isoformat()
        row["field_status"]["disclosed_at"] = "future_provider_update_date"
    elif issue in ("missing", "conflicting_rows"):
        row["disclosed_at"] = None
        row["field_status"]["disclosed_at"] = issue
    else:
        row["disclosed_at"] = (date.fromisoformat(world.current) - timedelta(days=1)).isoformat()
        row["field_status"]["disclosed_at"] = issue
    _assert_unknown(_financial_screen(world), "revenue")


def test_previous_report_update_conflict_invalidates_growth(world):
    row = world.reports[world.previous][0]
    row["disclosed_at"] = None
    row["field_status"]["disclosed_at"] = "conflicting_rows"
    _assert_unknown(_financial_screen(world, "revenue_growth_pct"), "revenue_growth_pct")


def test_wrong_financial_period_never_substitutes_for_requested_period(world):
    world.reports[world.current][0]["report_date"] = world.previous
    _assert_unknown(_financial_screen(world), "revenue")


@pytest.mark.parametrize("side", ["current", "previous"])
def test_conflicting_financial_identity_cannot_enter_growth(world, side):
    row = world.reports[getattr(world, side)][0]
    row.update(code="000001", exchange="sz")
    _assert_unknown(_financial_screen(world, "revenue_growth_pct"), "revenue_growth_pct")


def test_financial_field_conflict_is_unknown_even_with_numeric_payload(world):
    world.reports[world.current][0]["field_status"]["revenue"] = "conflicting_rows"
    out = _financial_screen(world)
    _assert_unknown(out, "revenue")
    assert out["missing_rows"][0]["reasons"]["revenue"] == "conflicting_rows"


def test_financial_provenance_preserves_adapter_metadata(world):
    out = _financial_screen(world)
    source = out["items"][0]["field_sources"]["revenue"]
    assert _timestamp(source["retrieved_at"]) == pytest.approx(world.current_retrieved)
    assert source["report_date"] == world.current
    assert source["basis"] == FIELD_CATALOG["revenue"]["basis"]
    assert source["source_field"] == FIELD_CATALOG["revenue"]["source_field"]
    assert source["native_field"] == FIELD_CATALOG["revenue"]["native_field"]
    assert source["disclosed_at_basis"] == "provider_update_date_not_first_disclosure"
    assert source["time_basis"] == "retrospective_current_materials"


def test_growth_provenance_includes_previous_input_source(world):
    def objects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from objects(child)

    out = _financial_screen(world, "revenue_growth_pct")
    provenance = out["items"][0]["field_sources"]["revenue_growth_pct"]
    assert any(
        part.get("report_date") == world.previous
        and part.get("source") == "fixture_financial_snapshot"
        and _timestamp(part.get("retrieved_at")) == pytest.approx(world.prior_retrieved)
        for part in objects(provenance)
    )


@pytest.mark.parametrize("issue", ["missing", "stale", "future"])
def test_quote_requires_usable_source_timestamp(world, issue):
    row = world.quotes["sh600001"]
    if issue == "missing":
        row.pop("data_as_of")
    else:
        row["data_as_of"] = (world.now + timedelta(days=-8 if issue == "stale" else 1)).isoformat()
    out = screening.screen_market({"price": {"min": 10}}, codes=["sh600001"])
    _assert_unknown(out, "price")


def test_quote_identity_is_checked_even_when_dictionary_key_matches(world):
    world.quotes["sh600001"].update(code="000001", exchange="sz")
    out = screening.screen_market({"price": {"min": 10}}, codes=["sh600001"])
    _assert_unknown(out, "price")


def test_full_directory_and_explicit_codes_keep_different_scope_metadata(world):
    full = screening.screen_market({"price": {"min": 10}})
    explicit = screening.screen_market({"price": {"min": 10}}, codes=["sh600001", "600001"])
    assert full["universe"]["universe_type"] == "exchange_listing_directory"
    assert explicit["universe"]["universe_type"] == "requested_symbols"
    assert full["coverage"]["unique_symbols"] == 3
    assert explicit["coverage"]["unique_symbols"] == 1
    assert len(world.calls["universe"]) == 1


def test_unverified_directory_cannot_produce_complete_market_screen(world):
    world.universe_complete = None
    out = screening.screen_market({"price": {"min": 10}})
    assert out["matched_count"] == 3 and out["partial"]
    assert out["coverage"]["universe_complete"] is None
    assert out["coverage"]["screen_complete"] is not True


def test_empty_explicit_scope_fetches_nothing(world):
    out = screening.screen_market({"price": {"min": 10}}, codes=[])
    assert out["ok"] and out["input_count"] == 0 and out["items"] == []
    assert not any(world.calls.values())


def test_missing_display_and_sort_fields_do_not_undo_a_proven_match(world):
    world.reports[world.current][0].update(revenue=None, roe=None)
    out = screening.screen_market(
        {"price": {"min": 10}},
        codes=["sh600001"],
        report_date=world.current,
        fields=["roe"],
        sort_by="revenue",
    )
    assert out["matched_count"] == 1 and out["missing_count"] == 0
    assert out["partial"] and not out["coverage"]["screen_complete"]
    assert not out["coverage"]["requested_fields_complete"]
    assert not out["coverage"]["sort_complete"]
    assert out["coverage"]["fields"]["roe"]["missing"] == 1
    assert out["coverage"]["fields"]["revenue"]["missing"] == 1
