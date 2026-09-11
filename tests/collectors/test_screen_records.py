"""Deterministic screening reports sample coverage separately from matches."""

from copy import deepcopy

import pytest
from screen_records import screen


@pytest.fixture
def sample():
    return {
        "universe": "用户提供的六行同行数据，非全市场",
        "as_of": "2026-08-31",
        "records": [
            {"name": "甲", "margin": "20%", "size": 3},
            {"name": "乙", "margin": 15, "size": 8},
            {"name": "丙", "margin": 5, "size": 1},
            {"name": "丁", "size": 2},
            {"name": "戊", "margin": "NaN"},
            {"name": "己", "margin": 25},
        ],
        "filters": {"margin": {"min": 10}},
    }


def test_counts_include_missing_and_matches_beyond_limit(sample):
    sample.update(limit=1, sort_by="size")
    original = deepcopy(sample)
    result = screen(sample)
    assert result["ok"]
    assert (
        result["input_count"],
        result["matched_count"],
        result["returned_count"],
        result["excluded_count"],
        result["missing_count"],
    ) == (6, 3, 1, 1, 2)
    assert result["items"][0]["name"] == "乙"
    assert result["coverage"] == "provided_records_only"
    assert result["universe"] == sample["universe"]
    assert result["missing_rows"] == [
        {"row_index": 3, "fields": ["margin"]},
        {"row_index": 4, "fields": ["margin"]},
    ]
    assert original == sample


def test_order_follows_input_unless_explicit_numeric_sort(sample):
    assert [row["name"] for row in screen(sample)["items"]] == ["甲", "乙", "己"]
    sample.update(sort_by="size", descending=False)
    assert [row["name"] for row in screen(sample)["items"]] == ["甲", "乙", "己"]
    sample["descending"] = True
    assert [row["name"] for row in screen(sample)["items"]] == ["乙", "甲", "己"]


def test_zero_matches_remains_a_successful_screen(sample):
    sample["filters"]["margin"]["min"] = 99
    result = screen(sample)
    assert result["ok"] and result["items"] == []
    assert result["matched_count"] == 0 and result["missing_count"] == 2


def test_filters_use_exact_provided_field_names_and_all_conditions(sample):
    sample["records"] = [
        {"pe": 8, "industry": "Software"},
        {"pe": 10, "industry": "Steel"},
        {"industry": "Software"},
    ]
    sample["filters"] = {"pe": {"min": 5, "max": 9}, "industry": {"contains": "soft"}}
    result = screen(sample)
    assert result["items"] == [sample["records"][0]]
    assert result["excluded_count"] == 1 and result["missing_count"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"universe": ""},
        {"as_of": None},
        {"filters": {}},
        {"limit": True},
        {"filters": {"margin": {"approximately": 10}}},
        {"filters": {"margin": {"min": float("nan")}}},
        {"filters": {"margin": {"min": True}}},
        {"filters": {"margin": {"min": 20, "max": 10}}},
        {"filters": {"margin": {"eq": None}}},
    ],
)
def test_ambiguous_or_invalid_screen_is_rejected(sample, changes):
    sample.update(changes)
    with pytest.raises(ValueError):
        screen(sample)
