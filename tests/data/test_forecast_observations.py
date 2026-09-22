"""共享预测观测的身份、来源和冲突边界。"""

from copy import deepcopy

import pytest
from scutio_data import research
from scutio_data.research.consensus import _forecast_observations


def report(day="2026-06-01", eps=2, **extra):
    return {
        "stockCode": "600519",
        "orgSName": "甲机构",
        "publishDate": day,
        "forecast_years": [2027],
        "predictThisYearEps": eps,
        "infoCode": day,
        **extra,
    }


def test_first_conflicting_day_cannot_compare_with_unambiguous_previous_day():
    env = research.consensus_revisions(
        "600519",
        reports=[
            report("2026-05-01", 1),
            report(eps=2, infoCode="a"),
            report(eps=3, infoCode="b"),
            report("2026-06-02", 4),
            report("2026-06-03", 5),
        ],
    )
    by_date = {
        day: [row for row in env["items"] if row["date"] == day]
        for day in ("2026-06-01", "2026-06-02", "2026-06-03")
    }
    assert env["ok"] and env["partial"]
    assert all(row["direction"] is None and row["change"] is None for row in by_date["2026-06-01"])
    assert all(row["comparison_reason"] == "ambiguous_current_day" for row in by_date["2026-06-01"])
    assert by_date["2026-06-02"][0]["comparison_reason"] == "ambiguous_previous_day"
    assert by_date["2026-06-03"][0]["direction"] == "up"
    assert by_date["2026-06-03"][0]["change"] == 1


def test_conflicting_first_observations_are_not_labelled_new():
    env = research.consensus_revisions("600519", reports=[report(eps=2), report(eps=3)])
    assert env["partial"] and len(env["items"]) == 2
    assert all(row["direction"] is None for row in env["items"])


def test_repeated_info_code_does_not_hide_conflicting_values():
    env = _forecast_observations("600519", [report(eps=2), report(eps=3)])
    assert env["ok"] and len(env["items"]) == 2
    assert all(row["conflict"] for row in env["items"])
    assert env["conflicts"][0]["eps_values"] == [2, 3]


def test_same_day_timestamps_do_not_claim_verified_version_order():
    env = _forecast_observations(
        "600519",
        [
            report(eps=2, published_at="2026-06-01T09:00:00+08:00"),
            report(eps=3, published_at="2026-06-01T12:00:00+08:00"),
        ],
    )
    assert all(row["conflict"] for row in env["items"])


def test_duplicate_predictions_count_once_and_retain_report_locations_on_reuse():
    env = _forecast_observations(
        "600519",
        [
            report(infoCode="a", pdf_url="https://example.org/a.pdf"),
            report(infoCode="b", pdf_url="https://example.org/b.pdf"),
            report(infoCode="a", pdf_url="https://example.org/a.pdf"),
        ],
    )
    assert env["ok"] and not env["partial"] and len(env["items"]) == 1
    assert {row["info_code"] for row in env["items"][0]["report_references"]} == {"a", "b"}
    reused = _forecast_observations("600519", env)
    assert reused["items"] == env["items"]


def test_standard_observations_revalidate_identity_and_ignore_claimed_direction():
    observations = research.consensus_revisions("600519", reports=[report()])
    observations["items"][0]["direction"] = "up"
    observations["items"][0]["change"] = 100
    env = _forecast_observations("600519", observations)
    assert env["ok"] and "direction" not in env["items"][0]
    observations["items"][0]["symbol"] = "sz000001"
    assert not _forecast_observations("600519", observations)["ok"]


def test_traceability_does_not_replace_source_date_with_fetch_time():
    reports = {
        "ok": True,
        "source": "research_fixture",
        "retrieved_at": "2026-09-20T10:00:00+08:00",
        "coverage": {"truncated": True},
        "errors": [{"page": 2, "error": "timeout"}],
        "items": [
            report(
                "2026-06-01 00:00:00",
                published_at="2026-06-02T17:00:00+08:00",
                available_at="2026-06-03T09:30:00+08:00",
                currency="CNY",
                eps_basis="post-placement shares",
                eps_definition="basic",
                version="original",
                pdf_url="https://example.org/a.pdf",
            )
        ],
    }
    unchanged = deepcopy(reports)
    env = _forecast_observations("600519", reports)
    row = env["items"][0]
    assert reports == unchanged
    assert row["date"] == "2026-06-01" and row["source_date"] == "2026-06-01 00:00:00"
    assert row["published_at"] == "2026-06-02T17:00:00+08:00"
    assert row["available_at"] == "2026-06-03T09:30:00+08:00"
    assert row["retrieved_at"] == env["input_retrieved_at"] == reports["retrieved_at"]
    assert row["report_url"] == "https://example.org/a.pdf"
    assert row["currency"] == "CNY" and row["eps_definition"] == "basic"
    assert row["eps_basis"] == "post-placement shares" and row["version"] == "original"
    assert env["partial"] and env["coverage"] == reports["coverage"]
    assert env["errors"] == reports["errors"]


def test_unverified_metadata_stays_unknown():
    row = _forecast_observations("600519", [report()])["items"][0]
    assert all(
        row[field] is None
        for field in (
            "published_at",
            "available_at",
            "currency",
            "eps_basis",
            "eps_definition",
            "retrieved_at",
        )
    )


@pytest.mark.parametrize("eps", [True, False, None, "-", float("nan"), float("inf"), 10**400])
def test_bad_eps_has_group_context_for_blocking_stale_fallback(eps):
    env = _forecast_observations("600519", [report(eps=eps)])
    assert env["ok"] and env["partial"] and not env["items"]
    assert env["skipped_reports"] == [
        {
            "index": 0,
            "info_code": "2026-06-01",
            "organization": "甲机构",
            "forecast_years": [2027],
            "date": "2026-06-01",
            "reason": "eps_missing_or_invalid",
        }
    ]


def test_missing_date_keeps_broker_and_year_in_skipped_records():
    env = _forecast_observations("600519", [report(day=None, infoCode="a")])
    skipped = env["skipped_reports"][0]
    assert skipped["reason"] == "publication_date_missing_or_invalid"
    assert skipped["organization"] == "甲机构" and skipped["forecast_years"] == [2027]
    assert skipped["info_code"] == "a"


@pytest.mark.parametrize(
    "field,values",
    [
        ("currency", ["CNY", "USD"]),
        ("eps_basis", ["before split", "after split"]),
        ("eps_definition", ["basic", "diluted"]),
    ],
)
def test_equal_eps_with_conflicting_units_is_not_deduplicated(field, values):
    reports = [report(**{field: value}) for value in values]
    env = _forecast_observations("600519", reports)
    assert env["partial"] and len(env["items"]) == 2
    assert env["conflicts"][0]["fields"] == [field]
    assert all(row["conflict"] for row in env["items"])


def test_adjacent_revisions_do_not_compare_explicitly_different_share_bases():
    env = research.consensus_revisions(
        "600519",
        reports=[
            report("2026-05-01", 2, eps_basis="before split"),
            report("2026-06-01", 1, eps_basis="after split"),
        ],
    )
    assert env["partial"]
    assert env["items"][0]["comparison_reason"] == "forecast_basis_mismatch"
    assert env["items"][0]["direction"] is None


def test_invalid_metadata_is_a_visible_gap_without_discarding_valid_reports():
    env = _forecast_observations(
        "600519", [report(currency={"value": "CNY"}), report("2026-05-01")]
    )
    assert env["ok"] and env["partial"] and len(env["items"]) == 1
    assert env["skipped_reports"][0]["reason"] == "forecast_metadata_invalid"
