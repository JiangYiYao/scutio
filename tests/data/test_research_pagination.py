"""非个股研报的有界分页、失败保留和检索覆盖范围。"""

from datetime import date

import pytest
from scutio_data import research
from scutio_data._providers import eastmoney_research as provider


def _responses(monkeypatch, *payloads):
    pending = iter(payloads)
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs["params"]))
        payload = next(pending)
        if isinstance(payload, Exception):
            raise payload

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return Response()

    monkeypatch.setattr(provider.eastmoney, "em_get", get)
    return calls


def test_industry_pagination_deduplicates_and_preserves_anonymous_rows(monkeypatch):
    calls = _responses(
        monkeypatch,
        {"data": [{"infoCode": "a"}, {"title": "same"}], "TotalPage": "2"},
        {"data": [{"infoCode": "a"}, {"infoCode": "b"}, {"title": "same"}], "totalPage": 2},
    )
    env = research.industry_reports("480000", begin="2025-01-01", end="2026-01-01")
    assert env["ok"] and env["coverage"]["complete"]
    assert len(env["items"]) == 4
    assert [params["pageNo"] for _, params in calls] == ["1", "2"]
    assert all(params["industryCode"] == "480000" for _, params in calls)
    assert calls[0][1]["endTime"] == "2026-01-01"


def test_broker_page_failure_keeps_earlier_reports(monkeypatch):
    calls = _responses(
        monkeypatch, {"data": [{"infoCode": "a"}], "TotalPage": 3}, TimeoutError("timed out")
    )
    env = research.broker_reports("macro")
    assert env["ok"] and env["partial"]
    assert env["items"][0]["_report_kind"] == "macro"
    assert env["errors"] == [{"page": 2, "error": "timed out"}]
    assert env["coverage"]["pages_fetched"] == 1
    assert not env["coverage"]["complete"]
    assert calls[0][0] == provider.REPORT_JG_API
    assert calls[0][1]["qType"] == "3"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"data": None},
        {"data": {}},
        {"data": ["bad"]},
        {"data": [], "success": False},
        {"data": [], "error": "blocked"},
        {"data": [], "TotalPage": "bad"},
        {"data": [{"infoCode": "a"}], "TotalPage": 0},
        {"data": [], "TotalPage": 3},
    ],
)
def test_invalid_report_response_is_not_empty_success(monkeypatch, payload):
    _responses(monkeypatch, payload)
    env = research.industry_reports()
    assert not env["ok"] and env["error"]
    assert env["items"] == []
    assert not env["coverage"]["complete"]


@pytest.mark.parametrize("total", [0, 1, None])
def test_empty_report_result_is_valid(monkeypatch, total):
    _responses(monkeypatch, {"data": [], "TotalPage": total})
    env = research.industry_reports()
    assert env["ok"] and env["items"] == []
    assert env["coverage"]["complete"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_pages": 0},
        {"max_pages": 51},
        {"max_pages": True},
        {"max_pages": 1.5},
        {"page_size": 101},
        {"page_size": "50"},
        {"kind": "unknown"},
        {"begin": "bad"},
        {"begin": "2026-02-01", "end": "2026-01-01"},
    ],
)
def test_invalid_report_query_makes_no_request(monkeypatch, kwargs):
    calls = _responses(monkeypatch)
    assert not research.broker_reports(**kwargs)["ok"]
    assert calls == []


def test_unknown_total_fetches_until_empty_and_uses_rolling_window(monkeypatch):
    calls = _responses(monkeypatch, {"data": [{"infoCode": "a"}]}, {"data": []})
    env = research.industry_reports(end="2026-09-10")
    assert env["coverage"]["complete"] and len(calls) == 2
    assert env["coverage"]["total_pages"] is None
    begin = date.fromisoformat(calls[0][1]["beginTime"])
    end = date.fromisoformat(calls[0][1]["endTime"])
    assert (end - begin).days == 730


@pytest.mark.parametrize("total", [None, 3])
def test_page_cap_discloses_incomplete_coverage(monkeypatch, total):
    calls = _responses(monkeypatch, {"data": [{"infoCode": "a"}], "TotalPage": total})
    env = research.industry_reports(max_pages=1)
    assert env["ok"] and not env["partial"] and not env["errors"]
    assert env["coverage"]["truncated"] and not env["coverage"]["complete"]
    assert len(calls) == 1


def test_local_search_preserves_fetch_failure_and_separate_search_limit(monkeypatch):
    _responses(
        monkeypatch,
        {
            "data": [
                {"infoCode": "a", "title": "机器人"},
                {"infoCode": "b", "title": "机器人产业"},
            ],
            "TotalPage": 3,
        },
        TimeoutError("timed out"),
    )
    env = research.local_report_search("机器人", limit=1)
    assert env["ok"] and env["partial"] and env["errors"][0]["page"] == 2
    assert env["coverage"]["returned"] == 2
    assert env["search_coverage"] == {
        "candidates": 2,
        "matched": 2,
        "returned": 1,
        "truncated": True,
    }
    assert len(env["items"]) == 1


def test_local_search_preserves_first_page_error(monkeypatch):
    _responses(monkeypatch, TimeoutError("timed out"))
    env = research.local_report_search("机器人")
    assert not env["ok"] and env["errors"][0]["page"] == 1
    assert env["coverage"]["pages_fetched"] == 0
