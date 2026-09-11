"""Batch completion, cooperative interruption and concurrency without public traffic."""

import sys
import threading
from functools import partial, wraps

import pytest
from scutio_data._runtime import processes, timeouts
from scutio_data.batch import fetch_many


def test_batch_runs_independent_calls_concurrently_and_preserves_results():
    barrier = threading.Barrier(2)
    payloads = [
        {"ok": True, "items": [{"amount": 7}], "partial": True},
        {"ok": False, "error": "source failed"},
    ]

    def call(index):
        barrier.wait(3)
        return payloads[index]

    result = fetch_many({"one": partial(call, 0), "two": partial(call, 1)}, max_workers=2)
    assert result["batch_state"] == "finished"
    assert list(result["results"]) == ["one", "two"]
    assert result["results"]["one"]["result"] is payloads[0]
    assert result["results"]["two"]["result"] is payloads[1]
    assert [row["state"] for row in result["results"].values()] == ["success", "failed"]


def test_batch_waits_for_slow_call_and_only_sends_metadata_progress():
    main_thread = threading.get_ident()
    release = threading.Event()
    seen = []
    payload = {"ok": True, "items": ["business-data-must-stay-in-final"]}

    def slow():
        assert release.wait(3)
        return payload

    def progress(event):
        assert threading.get_ident() == main_thread
        assert "business-data-must-stay-in-final" not in repr(event)
        assert event["batch_state"] == "running"
        seen.append(event)
        if event["completed"] == 1:
            release.set()
        raise RuntimeError("display unavailable")

    result = fetch_many({"fast": lambda: payload, "slow": slow}, on_progress=progress)
    assert seen and any(event["completed"] == 1 for event in seen)
    assert result["batch_state"] == "finished"
    assert all(item["state"] == "success" for item in result["results"].values())


def test_default_batch_has_no_shared_deadline_and_queued_calls_get_fresh_budget(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(timeouts.time, "monotonic", lambda: clock[0])

    @timeouts.source("query")
    def call():
        assert timeouts.remaining() == 30
        clock[0] += 25
        assert timeouts.remaining() == 5
        return {"ok": True}

    result = fetch_many({str(i): call for i in range(3)}, max_workers=1)
    assert result["batch_state"] == "finished"
    assert result["timing"]["total_seconds"] == 75
    assert [row["timing"]["queue_seconds"] for row in result["results"].values()] == [0, 25, 50]


def test_explicit_deadline_stops_running_worker_and_does_not_start_queued_call():
    queued = []

    def slow():
        processes.managed_run([sys.executable, "-c", "import time; time.sleep(60)"])
        raise AssertionError("slow worker must be stopped")

    with timeouts.request_timeout(0.3):
        result = fetch_many({"slow": slow, "queued": lambda: queued.append(True)}, max_workers=1)
    assert result["batch_state"] == "interrupted"
    assert result["results"]["slow"]["state"] == "timeout"
    assert result["results"]["queued"]["state"] == "not_started"
    assert result["results"]["queued"]["result"]["ok"] is False
    assert "items" not in result["results"]["queued"]["result"]
    assert queued == []
    assert result["timing"]["total_seconds"] < 3


def test_cancel_preserves_completed_result_and_reaps_worker_before_return():
    cancel = threading.Event()
    started = threading.Event()
    finished = threading.Event()
    completed = {"ok": True, "items": [{"value": 2}]}

    def slow():
        started.set()
        try:
            processes.managed_run([sys.executable, "-c", "import time; time.sleep(60)"])
        finally:
            finished.set()

    def progress(event):
        if event["completed"] and started.is_set():
            cancel.set()

    result = fetch_many(
        {"fast": lambda: completed, "slow": slow}, cancel_event=cancel, on_progress=progress
    )
    assert result["batch_state"] == "interrupted"
    assert result["results"]["fast"]["result"] is completed
    assert result["results"]["slow"]["state"] == "cancelled"
    assert finished.is_set()


def test_single_call_timeout_does_not_interrupt_other_independent_work():
    def timeout():
        raise timeouts.RequestTimeout("response")

    result = fetch_many({"timeout": timeout, "working": lambda: {"ok": True}})
    assert result["batch_state"] == "finished"
    assert result["results"]["timeout"]["state"] == "timeout"
    assert result["results"]["working"]["state"] == "success"


def test_keyboard_interrupt_during_progress_preserves_finished_data_and_cleans_up():
    entered = threading.Event()
    cleaned = threading.Event()
    finished = {"ok": True, "items": ["retained"]}

    def slow():
        entered.set()
        try:
            while True:
                timeouts.pause(0.02)
        finally:
            cleaned.set()

    def fast():
        assert entered.wait(3)
        return finished

    def progress(event):
        if event["completed"]:
            raise KeyboardInterrupt

    result = fetch_many({"slow": slow, "fast": fast}, on_progress=progress)
    assert result["batch_state"] == "interrupted"
    assert result["results"]["fast"]["result"] is finished
    assert result["results"]["slow"]["state"] == "cancelled"
    assert cleaned.is_set()


def test_nested_batch_stays_on_current_thread_and_shares_explicit_deadline():
    outer_deadlines = []

    def outer():
        thread = threading.get_ident()
        outer_deadlines.append(timeouts.current_deadline())

        def inner():
            assert threading.get_ident() == thread
            assert timeouts.current_deadline() == outer_deadlines[-1]
            return {"ok": True}

        return {"ok": True, "batch": fetch_many({"inner1": inner, "inner2": inner}, max_workers=8)}

    with timeouts.request_timeout(10):
        expected = timeouts.current_deadline()
        result = fetch_many({"outer": outer}, max_workers=1)
    assert result["results"]["outer"]["state"] == "success"
    assert outer_deadlines == [expected]
    assert all(
        row["state"] == "success"
        for row in result["results"]["outer"]["result"]["batch"]["results"].values()
    )


def test_nested_batch_propagates_live_source_progress_and_timings_once():
    release = threading.Event()
    main_thread = threading.get_ident()
    progress_stages = []

    def network():
        with timeouts.capture_diagnostics():
            timeouts.observe_event("request", provider="fixture", endpoint="financial")
            timeouts.add_timing("attempt_seconds", 0.25)
            timeouts.observe_event("worker", provider="fixture")
            assert release.wait(3), "outer progress did not receive the nested worker stage"
            timeouts.add_timing("worker_seconds", 0.125)
        return {"ok": True, "items": ["private-business-value"]}

    def cached():
        timeouts.observe_event("cache_hit", provider="fixture", endpoint="financial")
        return {"ok": True}

    def domain():
        return {
            "ok": True,
            "batch": fetch_many({"network": network, "cached1": cached, "cached2": cached}),
        }

    def progress(event):
        assert threading.get_ident() == main_thread
        assert "private-business-value" not in repr(event)
        stage = event["running"].get("domain")
        progress_stages.append(stage)
        if stage == "worker":
            release.set()

    result = fetch_many({"domain": domain}, on_progress=progress)
    row = result["results"]["domain"]
    assert "worker" in progress_stages and row["state"] == "success"
    assert row["timing"]["attempt_seconds"] == 0.25
    assert row["timing"]["worker_seconds"] == 0.125
    stages = [event["stage"] for event in row["execution"]]
    assert stages.count("request") == stages.count("worker") == 1
    assert stages.count("cache_hit") == 2
    nested = row["result"]["batch"]["results"]["network"]
    assert nested["state"] == "success"
    assert nested["timing"]["attempt_seconds"] == row["timing"]["attempt_seconds"]


def test_parallel_nested_diagnostics_are_isolated_and_parent_totals_are_thread_safe():
    barrier = threading.Barrier(4)

    def domain(index):
        def inner():
            barrier.wait(3)
            timeouts.observe_event("request", provider="fixture", endpoint=str(index))
            for _ in range(500):
                timeouts.add_timing("attempt_seconds", 0.25)
            return {"ok": True}

        return {"ok": True, "batch": fetch_many({"inner": inner})}

    with timeouts.capture_diagnostics() as diagnostics:
        result = fetch_many({str(i): partial(domain, i) for i in range(4)})
    assert diagnostics["timing"]["attempt_seconds"] == 500
    assert len(diagnostics["events"]) == 4
    for name, row in result["results"].items():
        assert row["state"] == "success"
        assert row["timing"]["attempt_seconds"] == 125
        assert [event["endpoint"] for event in row["execution"]] == [name]


def test_parallel_calls_copy_context_independently():
    barrier = threading.Barrier(2)

    def call(seconds):
        with timeouts.request_timeout(10, connect_seconds=seconds):
            barrier.wait(3)
            assert timeouts.transport_timeout()[0] == seconds
            return {"ok": True}

    with timeouts.request_timeout(10, connect_seconds=3):
        result = fetch_many({"a": partial(call, 1), "b": partial(call, 2)})
        assert timeouts.transport_timeout()[0] == 3
    assert all(row["state"] == "success" for row in result["results"].values())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_workers": 0},
        {"max_workers": True},
        {"on_progress": 1},
        {"cancel_event": object()},
    ],
)
def test_batch_validates_all_options_before_starting_any_call(kwargs):
    calls = []
    with pytest.raises((ValueError, TypeError)):
        fetch_many({"x": lambda: calls.append(True)}, **kwargs)
    assert calls == []


@pytest.mark.parametrize("exception", [ValueError, TypeError])
def test_arbitrary_exception_text_is_not_exposed_and_empty_batch_finishes(exception):
    def fail():
        raise exception("contains-sensitive-token")

    result = fetch_many({"bad": fail})
    assert "contains-sensitive-token" not in repr(result)
    assert result["results"]["bad"]["result"] == {
        "ok": False,
        "error": exception.__name__,
        "error_code": "failed",
    }
    assert fetch_many({})["batch_state"] == "finished"


@pytest.mark.parametrize("wrapped", [False, True], ids=["partial", "wrapper"])
def test_batch_explains_real_api_argument_errors_and_preserves_valid_peers(wrapped):
    from scutio_data import announcements, capital, feeds, market

    calls = {
        "bars": partial(market.security_bars, "600519", num=35),
        "news": partial(feeds.stock_news, "600519", num=25),
        "announcements": partial(announcements.stock_announcements, "600519", num=25),
        "lockup": partial(announcements.lockup_expiry, "600519"),
    }
    if wrapped:
        calls = {name: (lambda call=call: call()) for name, call in calls.items()}
    # This public facade is not decorated; partial binding must cover it too.
    calls["dragon_tiger"] = partial(capital.dragon_tiger_board, "600519")
    payload = {"ok": True, "items": [{"value": 7}]}
    calls["valid"] = lambda: payload
    result = fetch_many(calls)
    assert result["batch_state"] == "finished"
    assert result["results"]["valid"]["result"] is payload
    expected = {
        "bars": ("security_bars", "count"),
        "news": ("stock_news", "page_size"),
        "announcements": ("stock_announcements", "page_size"),
        "lockup": ("lockup_expiry", "trade_date"),
        "dragon_tiger": ("dragon_tiger_board", "trade_date"),
    }
    for name, (function, parameter) in expected.items():
        item = result["results"][name]
        assert item["state"] == "failed"
        assert item["result"]["error_code"] == "invalid_arguments"
        assert function in item["result"]["error"]
        assert parameter in item["result"]["error"]


def test_argument_diagnostics_exclude_values_unknown_keywords_defaults_and_annotations():
    import inspect

    entered = []

    def query(code, count="private-default"):
        entered.append((code, count))
        return {"ok": True}

    query.__annotations__ = {"code": "private-annotation"}
    query = timeouts.operation("query")(query)
    bad = partial(query, "private-value", **{"private-keyword": "private-value"})
    result = fetch_many({"bad": bad, "wrapped": lambda: bad()})
    assert "private-" not in repr(result)
    assert entered == []
    for item in result["results"].values():
        assert item["result"]["error_code"] == "invalid_arguments"
        assert "required parameters: code; optional parameters: count" in item["error"]
    with pytest.raises(TypeError, match="required parameters: code") as error:
        bad()
    assert "private-" not in str(error.value)
    assert error.value.__suppress_context__
    assert str(inspect.signature(query)) == str(inspect.signature(query.__wrapped__))
    assert query("600519", count=4)["ok"]
    assert entered == [("600519", 4)]


def test_batch_accepts_zero_argument_wrapper_that_supplies_underlying_arguments():
    @timeouts.operation("query")
    def query(code):
        return {"ok": True, "code": code}

    @wraps(query)
    def request():
        return query("600519")

    result = fetch_many({"request": request})
    assert result["results"]["request"]["state"] == "success"
    assert result["results"]["request"]["result"] == request()


def test_batch_argument_binding_handles_positional_and_keyword_only_parameters():
    entered = []

    def query(code, /, *, count):
        entered.append(code)
        return {"ok": True}

    result = fetch_many(
        {
            "positional_only": partial(query, code="600519", count=4),
            "keyword_only": partial(query, "600519", 4),
            "missing": partial(query, "600519"),
            "valid": partial(query, "600519", count=4),
        }
    )
    assert entered == ["600519"]
    for name in ("positional_only", "keyword_only", "missing"):
        assert result["results"][name]["result"]["error_code"] == "invalid_arguments"
    assert result["results"]["valid"]["state"] == "success"


@pytest.mark.parametrize("value", [None, [], {"ok": 1}, {"items": []}])
def test_invalid_domain_envelope_cannot_count_as_success(value):
    result = fetch_many({"invalid": lambda: value})
    item = result["results"]["invalid"]
    assert item["state"] == "failed"
    assert item["result"]["error_code"] == "invalid_result_envelope"
