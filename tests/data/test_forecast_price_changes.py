"""固定窗口预测与价格比较的可复核数值和失效边界。"""

import json
import math
from copy import deepcopy

import pytest
from scutio_data import research
from scutio_data.research.signals import forecast_price_changes

START = "2026-07-01"
END = "2026-09-01"


def report(day, eps, organization="甲机构", **extra):
    return {
        "stockCode": "600519",
        "symbol": "sh600519",
        "orgSName": organization,
        "publishDate": day,
        "forecast_years": [2027],
        "predictThisYearEps": eps,
        "infoCode": organization + str(day),
        "currency": "CNY",
        "eps_basis": "original shares",
        "eps_definition": "basic annual EPS",
        "source": "test_reports",
        **extra,
    }


def reports(*items, **extra):
    return {
        "ok": True,
        "symbol": "sh600519",
        "source": "test_reports",
        "items": list(items) or [report("2026-06-01", 2), report("2026-08-01", 2.4)],
        **extra,
    }


def bars(p0=20, p1=22, **extra):
    return {
        "ok": True,
        "symbol": "sh600519",
        "source": "test_bars",
        "frequency": "D",
        "adjust": "none",
        "currency": "CNY",
        "bars": [{"datetime": START, "close": p0}, {"datetime": END, "close": p1}],
        **extra,
    }


def basis(**extra):
    return {
        "symbol": "sh600519",
        "start_date": START,
        "end_date": END,
        "status": "unchanged",
        "cash_dividends": "none",
        "evidence": ["https://example.org/share-count-verification"],
        **extra,
    }


def run(report_env=None, bar_env=None, **extra):
    parameters = {
        "start_date": START,
        "end_date": END,
        "fiscal_year": 2027,
        "max_age_days": 120,
        "eps_floor": 0.01,
        "share_basis": basis(),
        **extra,
    }
    return forecast_price_changes(
        "600519",
        reports() if report_env is None else report_env,
        bars() if bar_env is None else bar_env,
        **parameters,
    )


def test_manual_example_uses_endpoint_eps_and_raw_prices_without_network(monkeypatch):
    from scutio_data._providers.akshare import client

    monkeypatch.setattr(
        client, "fetch", lambda *a, **k: pytest.fail("pure calculation must not fetch")
    )
    report_env, bar_env = reports(), bars()
    original = deepcopy((report_env, bar_env))
    env = run(report_env, bar_env)
    assert (report_env, bar_env) == original
    assert env["ok"] and not env["partial"] and env["matched"] is True
    row = env["items"][0]
    assert row["eps_change"] == pytest.approx(0.4)
    assert row["eps_change_pct"] == pytest.approx(20)
    assert row["price_change_pct"] == pytest.approx(10)
    assert row["pe0"] == 10 and row["pe1"] == pytest.approx(9.1666666667)
    assert row["pe_change_pct"] == pytest.approx(-8.3333333333)
    assert env["summary"]["matched"] == env["summary"]["paired"] == 1
    assert env["mode"] == "retrospective_current_materials"
    assert env["point_in_time_verified"] is False


def test_invalid_eps_published_before_endpoint_close_blocks_older_match():
    env = run(
        reports(
            report("2026-06-01", 2),
            report("2026-08-01", 2.4),
            report(END, "-", published_at=END + "T14:00:00+08:00"),
        )
    )
    assert env["ok"] and env["partial"]
    assert env["matched"] is None
    assert "unusable_forecast_records" in env["items"][0]["reasons"]


def test_after_close_conflict_does_not_poison_an_available_endpoint():
    env = run(
        reports(
            report("2026-06-01", 2),
            report(END, 2.4, published_at=END + "T14:00:00+08:00"),
            report(END, 3, published_at=END + "T16:00:00+08:00"),
        )
    )
    assert env["matched"] is True
    assert env["conflicts"]  # Full-input audit still retains the later disagreement.
    endpoint = env["items"][0]["end_forecast"]
    assert endpoint["eps"] == 2.4
    assert all(not row["conflict"] for row in endpoint["observations"])


def test_two_conflicting_forecasts_available_before_close_still_block():
    env = run(
        reports(
            report("2026-06-01", 2),
            report(END, 2.4, published_at=END + "T10:00:00+08:00"),
            report(END, 3, published_at=END + "T14:00:00+08:00"),
        )
    )
    assert env["matched"] is None
    assert "end_forecast_conflict" in env["items"][0]["reasons"]


def test_unchanged_report_is_reported_even_when_share_basis_is_unknown():
    env = run(reports(report("2026-06-01", 2)), share_basis=None)
    assert env["matched"] is None and not env["items"][0]["eps_comparable"]
    assert env["items"][0]["unchanged_report"] is True
    assert env["summary"]["not_updated"] == 1


@pytest.mark.parametrize(
    "e0,e1,p0,p1,metric",
    [
        (0.001, 1e303, 20, 22, "eps_change_pct"),
        (1e300, 1, 0.01, 1e4, "pe_change_pct"),
    ],
)
def test_even_median_of_extreme_finite_changes_is_finite(e0, e1, p0, p1, metric):
    env = run(
        reports(
            report("2026-06-01", e0, "A"),
            report("2026-08-01", e1, "A"),
            report("2026-06-01", e0, "B"),
            report("2026-08-01", e1, "B"),
        ),
        bars(p0, p1),
        eps_floor=0,
    )
    assert env["summary"]["metrics"][metric]["median"] == pytest.approx(1e308)
    json.dumps(env, allow_nan=False)


def test_invalid_eps_after_endpoint_close_does_not_block_available_pair():
    env = run(
        reports(
            report("2026-06-01", 2),
            report("2026-08-01", 2.4),
            report(END, "-", published_at=END + "T16:00:00+08:00"),
        )
    )
    assert env["ok"] and env["matched"] is True


def test_new_valid_forecasts_supersede_an_older_invalid_record():
    env = run(
        reports(
            report("2026-04-01", "-"),
            report("2026-06-01", 2),
            report("2026-08-01", 2.4),
        )
    )
    assert env["ok"] and env["matched"] is True
    assert env["skipped_reports"]


def test_late_availability_does_not_reset_forecast_age():
    env = run(
        reports(
            report("2026-06-01", 2),
            report("2026-08-01", 2.4, available_at="2026-08-31T14:00:00+08:00"),
        ),
        max_age_days=30,
    )
    assert env["matched"] is None
    assert env["items"][0]["end_forecast"]["age_days"] == 31
    assert "end_forecast_stale" in env["items"][0]["reasons"]


def test_fixed_window_differs_from_last_adjacent_revision():
    raw = reports(report("2026-06-01", 2), report("2026-07-20", 2.8), report("2026-08-01", 2.4))
    adjacent = research.consensus_revisions("600519", reports=raw)
    assert adjacent["items"][0]["direction"] == "down"
    result = run(raw)
    assert result["items"][0]["direction"] == "up"
    assert result["items"][0]["eps_change_pct"] == pytest.approx(20)
    normalized_result = run(adjacent)
    assert normalized_result["items"] == result["items"]


def test_same_broker_many_reports_and_duplicate_reprints_count_once():
    env = run(
        reports(
            report("2026-06-01", 2),
            report("2026-07-20", 2.1),
            report("2026-08-01", 2.4),
            report("2026-08-01", 2.4, infoCode="reprint"),
        )
    )
    assert env["summary"]["organizations"] == env["summary"]["paired"] == 1
    assert len(env["items"][0]["end_forecast"]["observations"]) == 1


def test_conflicting_latest_day_blocks_endpoint_without_retreating():
    env = run(
        reports(
            report("2026-06-01", 2),
            report("2026-07-20", 2.2),
            report("2026-08-01", 2.4, infoCode="a"),
            report("2026-08-01", 1.8, infoCode="b"),
        )
    )
    row = env["items"][0]
    assert env["partial"] and row["end_forecast"]["date"] == "2026-08-01"
    assert "end_forecast_conflict" in row["reasons"]
    assert row["direction"] is None and row["matched"] is None


def test_cross_calendar_year_comparison_still_uses_absolute_target_year():
    raw = reports(
        report("2026-06-01", 1, forecast_years=[2026, 2027], predictNextYearEps=2),
        report("2026-08-01", 2.4, forecast_years=[2027, 2028], predictNextYearEps=99),
    )
    env = run(raw)
    assert env["items"][0]["eps_change_pct"] == pytest.approx(20)


def test_other_fiscal_year_is_not_used_as_missing_endpoint():
    env = run(
        reports(
            report("2026-06-01", 2, forecast_years=[2026]),
            report("2026-08-01", 2.4),
        )
    )
    row = env["items"][0]
    assert row["coverage_status"] == "new_coverage" and row["direction"] is None
    assert row["matched"] is None and env["summary"]["paired"] == 0


@pytest.mark.parametrize("reuse_normalized", [False, True])
def test_invalid_prediction_in_other_year_does_not_block_valid_target_year(reuse_normalized):
    raw = reports(
        report("2026-06-01", 2, forecast_years=[2027, 2028], predictNextYearEps=None),
        report("2026-08-01", 2.4, forecast_years=[2027, 2028], predictNextYearEps=None),
    )
    env = run(research.consensus_revisions("600519", reports=raw) if reuse_normalized else raw)
    assert env["items"][0]["matched"] is True
    assert not env["items"][0]["reasons"] and not env["skipped_reports"]
    assert env["partial"] and env["coverage"]["companies"]["missing"] == 1
    provenance = env["input_provenance"]["forecasts"]
    assert provenance["partial"]
    assert len(provenance["skipped_reports"]) == 2
    assert {row["date"] for row in provenance["skipped_reports"]} == {
        "2026-06-01",
        "2026-08-01",
    }
    assert all(
        row["forecast_years"] == [2028] and row["reason"] == "eps_missing_or_invalid"
        for row in provenance["skipped_reports"]
    )


def test_invalid_report_after_end_does_not_block_existing_window_pair():
    env = run(
        reports(
            report("2026-06-01", 2),
            report("2026-08-01", 2.4),
            report("2026-10-01", None),
        )
    )
    assert env["items"][0]["matched"] is True


@pytest.mark.parametrize("time_field", ["published_at", "available_at"])
@pytest.mark.parametrize("organization", ["甲机构", "仅未来覆盖"])
@pytest.mark.parametrize("eps", [3, None])
def test_future_invalid_timestamp_is_audited_without_blocking_window(time_field, organization, eps):
    timestamp = "2026-10-01T10:00:00"
    raw = reports(
        report("2026-06-01", 2),
        report("2026-08-01", 2.4),
        report("2026-10-01", eps, organization, **{time_field: timestamp}),
    )
    original = deepcopy(raw)
    env = run(raw)
    assert raw == original
    assert env["ok"] and env["partial"] and env["matched"] is True
    assert env["summary"]["organizations"] == env["summary"]["computable"] == 1
    assert not env["items"][0]["reasons"] and not env["skipped_reports"]
    provenance = env["input_provenance"]["forecasts"]
    assert provenance["partial"]
    assert len(provenance["skipped_reports"]) == 1
    skipped = provenance["skipped_reports"][0]
    assert skipped["date"] == "2026-10-01" and skipped["forecast_years"] == [2027]
    assert skipped[time_field] == timestamp and skipped["organization"] == organization
    assert skipped["info_code"] == organization + "2026-10-01"
    assert skipped["reason"] == (
        "invalid_availability" if eps is not None else "eps_missing_or_invalid"
    )


@pytest.mark.parametrize("known_field", ["published_at", "available_at"])
@pytest.mark.parametrize(
    "known_time,expected_match",
    [
        ("2026-10-01T10:00:00+08:00", True),
        (END, True),
        (END + "T08:00:00Z", True),  # 16:00 in Shanghai, after the endpoint close.
        (END + "T06:00:00Z", None),  # Before close cannot rule out the malformed record.
    ],
)
@pytest.mark.parametrize("eps", [3, None])
@pytest.mark.parametrize("reuse_normalized", [False, True])
def test_known_availability_bounds_survive_another_invalid_timestamp(
    known_field, known_time, expected_match, eps, reuse_normalized
):
    invalid_field = "available_at" if known_field == "published_at" else "published_at"
    raw = reports(
        report("2026-06-01", 2),
        report("2026-08-01", 2.4),
        report(
            "2026-08-15",
            eps,
            **{known_field: known_time, invalid_field: "2026-08-15T10:00:00"},
        ),
    )
    env = run(research.consensus_revisions("600519", reports=raw) if reuse_normalized else raw)
    assert env["ok"] and env["partial"] and env["matched"] is expected_match
    assert len(env["input_provenance"]["forecasts"]["skipped_reports"]) == 1
    if expected_match is True:
        assert not env["skipped_reports"] and not env["items"][0]["reasons"]
    else:
        assert len(env["skipped_reports"]) == 1
        assert "unusable_forecast_records" in env["items"][0]["reasons"]


def test_qualified_company_hit_is_not_assembled_from_different_brokers():
    env = run(
        reports(
            report("2026-06-01", 0.005, "微小基数"),
            report("2026-08-01", 0.02, "微小基数"),
            report("2026-06-01", 2, "下修机构"),
            report("2026-08-01", 1.9, "下修机构"),
        ),
        bars(p0=20, p1=18),
    )
    assert env["summary"]["up"] == env["summary"]["down"] == 1
    assert any(
        row["pe_change_pct"] is not None and row["pe_change_pct"] < 0 for row in env["items"]
    )
    assert env["matched"] is False and env["summary"]["matched"] == 0


def test_new_coverage_and_unupdated_report_do_not_count_as_upgrades():
    env = run(reports(report("2026-06-01", 2, "未更新"), report("2026-08-01", 2.4, "新增")))
    assert env["summary"]["new_coverage"] == 1
    assert env["summary"]["not_updated"] == env["summary"]["flat"] == 1
    assert env["summary"]["up"] == 0
    continued = next(row for row in env["items"] if row["organization"] == "未更新")
    assert continued["start_forecast"]["age_days"] == 30
    assert continued["end_forecast"]["age_days"] == 92
    assert continued["eps_change"] == 0 and continued["matched"] is False


@pytest.mark.parametrize(
    "e0,e1,direction,reason",
    [
        (-1, -0.5, "up", "nonpositive_eps"),
        (-1, 1, "up", "nonpositive_eps"),
        (0, 1, "up", "nonpositive_eps"),
        (1, 0, "down", "nonpositive_eps"),
        (0.005, 0.1, "up", "near_zero_eps"),
        (0.01, 0.1, "up", "near_zero_eps"),
    ],
)
def test_nonpositive_and_near_zero_eps_retain_values_without_growth_or_pe(
    e0, e1, direction, reason
):
    env = run(reports(report("2026-06-01", e0), report("2026-08-01", e1)))
    row = env["items"][0]
    assert row["eps_change"] == pytest.approx(e1 - e0) and row["direction"] == direction
    assert row["eps_change_pct"] is None and row["pe0"] is None and row["pe1"] is None
    assert reason in row["reasons"] and row["matched"] is None


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), "-", None])
def test_invalid_latest_eps_is_visible_and_does_not_fall_back_to_old_value(value):
    env = run(reports(report("2026-06-01", 2), report("2026-08-01", value)))
    assert env["ok"] and env["partial"] and env["skipped_reports"]
    assert env["items"][0]["matched"] is None
    assert "unusable_forecast_records" in env["items"][0]["reasons"]


def test_stale_forecast_cannot_be_a_computable_unchanged_pair():
    env = run(reports(report("2026-06-01", 2)), max_age_days=60)
    row = env["items"][0]
    assert "end_forecast_stale" in row["reasons"]
    assert row["coverage_status"] == "old_only"
    assert env["summary"]["old_only"] == 1
    assert row["end_forecast"]["age_days"] == 92 and row["matched"] is None


def test_empty_reports_is_missing_coverage_not_no_hit_evidence():
    env = run(reports(items=[]))
    assert env["ok"] and env["partial"] and not env["items"] and env["matched"] is None
    assert env["coverage"]["companies"]["computable"] == 0


def test_report_date_only_is_not_eligible_for_same_day_close():
    env = run(reports(report("2026-06-01", 2), report(END, 3)))
    assert env["items"][0]["end_forecast"]["eps"] == 2
    assert env["items"][0]["unchanged_report"]


@pytest.mark.parametrize(
    "timestamp,expected_eps",
    [
        ("2026-09-01T14:00:00+08:00", 3),
        ("2026-09-01T16:00:00+08:00", 2),
        ("2026-09-02T09:00:00+08:00", 2),
    ],
)
def test_explicit_publication_time_cannot_be_used_before_availability(timestamp, expected_eps):
    env = run(reports(report("2026-06-01", 2), report(END, 3, published_at=timestamp)))
    assert env["items"][0]["end_forecast"]["eps"] == expected_eps


def test_later_availability_overrides_earlier_source_and_publication_date():
    env = run(
        reports(
            report("2026-06-01", 2),
            report(
                "2026-08-01",
                3,
                published_at="2026-08-01T10:00:00+08:00",
                available_at="2026-09-02T10:00:00+08:00",
            ),
        )
    )
    assert env["items"][0]["end_forecast"]["eps"] == 2


def test_bad_publication_time_does_not_allow_stale_fallback_signal():
    env = run(
        reports(
            report("2026-06-01", 2),
            report("2026-08-01", 3, published_at="2026-08-01T10:00:00"),
        )
    )
    assert env["partial"] and env["items"][0]["matched"] is None
    skipped = env["skipped_reports"][0]
    assert skipped["reason"] == "invalid_availability"
    assert skipped["date"] == skipped["source_date"] == "2026-08-01"
    assert skipped["forecast_years"] == [2027]
    assert skipped["published_at"] == "2026-08-01T10:00:00"
    assert env["input_provenance"]["forecasts"]["skipped_reports"] == [skipped]


def test_earlier_source_date_does_not_make_invalid_publication_safe():
    env = run(
        reports(
            report("2026-05-01", 3, published_at="2026-08-01T10:00:00"),
            report("2026-06-01", 2),
            report("2026-08-01", 2.4),
        )
    )
    assert env["matched"] is None
    assert "unusable_forecast_records" in env["items"][0]["reasons"]


@pytest.mark.parametrize(
    "share_evidence,reason",
    [
        (None, "share_basis_unverified"),
        (basis(status="unknown"), "share_basis_unknown"),
        (basis(status="changed"), "share_basis_changed"),
        (basis(evidence=[]), "share_basis_unverified"),
    ],
)
def test_unverified_or_changed_share_basis_blocks_automatic_comparison(share_evidence, reason):
    env = run(share_basis=share_evidence)
    row = env["items"][0]
    assert reason in row["reasons"] and row["direction"] is None and row["matched"] is None
    assert row["start_forecast"]["eps"] == 2 and row["end_forecast"]["eps"] == 2.4


@pytest.mark.parametrize("field", ["currency", "eps_basis", "eps_definition"])
def test_unknown_eps_definition_is_a_gap_not_an_assumed_match(field):
    env = run(reports(report("2026-06-01", 2, **{field: None}), report("2026-08-01", 2.4)))
    assert field + "_unknown" in env["items"][0]["reasons"]
    assert env["items"][0]["direction"] is None


def test_price_and_eps_currency_must_match():
    env = run(bar_env=bars(currency="USD"))
    assert "price_eps_currency_mismatch" in env["items"][0]["reasons"]
    assert env["matched"] is None


def test_cash_dividend_is_disclosed_without_changing_actual_price_pe():
    env = run(share_basis=basis(cash_dividends="present"))
    row = env["items"][0]
    assert row["cash_dividends"] == env["cash_dividends"] == "present"
    assert row["pe0"] == 10 and row["pe1"] == pytest.approx(9.1666666667)
    assert "现金分红" in env["warning"]


@pytest.mark.parametrize("side", ["envelope", "row"])
def test_price_identity_mismatch_is_rejected(side):
    price_env = bars()
    if side == "envelope":
        price_env["symbol"] = "sz000001"
    else:
        price_env["bars"][1]["symbol"] = "sz000001"
    assert not run(bar_env=price_env)["ok"]


def test_missing_price_identity_is_rejected():
    price_env = bars()
    price_env.pop("symbol")
    assert not run(bar_env=price_env)["ok"]


@pytest.mark.parametrize("adjust", ["qfq", "hfq", None])
def test_adjusted_or_unknown_price_basis_never_enters_pe(adjust):
    env = run(bar_env=bars(adjust=adjust))
    assert "prices_must_be_unadjusted" in env["items"][0]["reasons"]
    assert env["items"][0]["pe0"] is None and env["matched"] is None


def test_missing_endpoint_does_not_silently_use_earlier_close():
    price_env = bars()
    price_env["bars"][1]["datetime"] = "2026-08-31"
    env = run(bar_env=price_env)
    assert "end_price_missing_or_invalid" in env["items"][0]["reasons"]
    assert env["items"][0]["end_price"] is None and env["matched"] is None


@pytest.mark.parametrize("price", [0, -1, True, False, float("nan"), float("inf"), None, 10**400])
def test_zero_boolean_nonfinite_or_missing_prices_cannot_be_real_closes(price):
    env = run(bar_env=bars(p1=price))
    assert env["ok"] and env["partial"] and env["matched"] is None
    assert "end_price_missing_or_invalid" in env["items"][0]["reasons"]


def test_conflicting_duplicate_endpoint_is_not_last_row_wins():
    price_env = bars()
    price_env["bars"].append({"datetime": END, "close": 25})
    env = run(bar_env=price_env)
    assert "end_price_conflict" in env["items"][0]["reasons"]
    assert env["matched"] is None


def test_partial_fetch_retains_calculable_pair_and_upstream_failures():
    report_env = reports(
        partial=True, errors=[{"page": 2, "error": "timeout"}], coverage={"truncated": True}
    )
    price_env = bars(partial=True, errors=[{"source": "first", "error": "timeout"}])
    env = run(report_env, price_env)
    assert env["ok"] and env["partial"] and env["items"][0]["matched"] is True
    assert env["input_provenance"]["forecasts"]["errors"] == report_env["errors"]
    assert env["input_provenance"]["bars"]["errors"] == price_env["errors"]
    assert env["coverage"]["forecasts"] == {"truncated": True}


def test_failed_reports_and_successful_no_match_are_distinct():
    failed = run(reports(ok=False, error="unavailable", errors=[{"page": 1}]))
    no_hit = run(reports(report("2026-06-01", 2), report("2026-08-01", 1.9)))
    assert not failed["ok"] and failed["input_forecasts"]["errors"] == [{"page": 1}]
    assert no_hit["ok"] and not no_hit["partial"] and no_hit["matched"] is False


def test_failed_price_fetch_does_not_turn_into_successful_signal():
    env = run(bar_env=bars(ok=False, error="unavailable"))
    assert env["partial"] and env["matched"] is None
    assert env["coverage"]["companies"]["fetched"] == 0


def test_limit_only_changes_display_not_broker_totals_or_company_hit():
    raw = reports(
        report("2026-06-01", 2, "A"),
        report("2026-08-01", 1.8, "A"),
        report("2026-06-01", 2, "B"),
        report("2026-08-01", 2.4, "B"),
    )
    full, limited = run(raw), run(raw, limit=1)
    assert full["summary"] == limited["summary"]
    assert limited["matched"] is True and limited["summary"]["matched"] == 1
    assert limited["items"][0]["matched"] is False
    assert limited["coverage"]["organizations"] == {"total": 2, "returned": 1, "truncated": True}
    assert limited["partial"] and not full["partial"]
    assert (
        limited["coverage"]["companies"]["missing"] == full["coverage"]["companies"]["missing"] == 0
    )
    assert limited["summary"]["disagreement"]


def test_unmatched_brokers_do_not_form_a_synthetic_pair():
    env = run(reports(report("2026-06-01", 2, "A"), report("2026-08-01", 3, "B")))
    assert env["summary"]["up"] == 0
    assert env["summary"]["new_coverage"] == 1 and env["matched"] is False


def test_finite_inputs_that_overflow_ratios_cannot_produce_infinite_signal():
    env = run(reports(report("2026-06-01", 1e-300), report("2026-08-01", 1e300)), eps_floor=0)
    row = env["items"][0]
    assert row["matched"] is None and env["partial"]
    assert all(
        value is None or math.isfinite(value)
        for value in (
            row["eps_change_pct"],
            row["price_change_pct"],
            row["pe0"],
            row["pe1"],
            row["pe_change_pct"],
        )
    )
