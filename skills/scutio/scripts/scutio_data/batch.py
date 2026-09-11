"""Synchronous, bounded execution of explicit public data calls; no research workflow."""

from __future__ import annotations

import queue
import time
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import ContextVar, copy_context

from scutio_data._runtime.arguments import InvalidArguments, validate_arguments
from scutio_data._runtime.timeouts import (
    CancellationToken,
    RequestCancelled,
    RequestTimeout,
    cancellation_scope,
    capture_diagnostics,
    check_cancelled,
    current_deadline,
)

_depth = ContextVar("scutio_batch_depth", default=0)


def in_batch():
    return _depth.get() > 0


def fetch_many(requests, *, max_workers=4, on_progress=None, cancel_event=None):
    """Wait for every selected call to finish, returning its unchanged domain result.

    Each mapping value is a zero-argument callable, usually functools.partial of
    a Scutio public API. No default batch deadline is imposed. An enclosing
    request_timeout shares its absolute deadline with every task, including its
    queue time. Cancellation is cooperative for Python code and terminates owned
    HTTP/AKShare workers. A nested batch runs sequentially on the calling thread.
    on_progress receives metadata only, serially on the calling thread.
    """
    if not isinstance(requests, Mapping):
        raise TypeError("requests must be a mapping of names to callable objects")
    calls = dict(requests)
    if any(
        not isinstance(name, str) or not name.strip() or not callable(call)
        for name, call in calls.items()
    ):
        raise ValueError("each request needs a nonempty string name and a callable")
    if type(max_workers) is not int or max_workers < 1:
        raise ValueError("max_workers must be a positive integer")
    if on_progress is not None and not callable(on_progress):
        raise TypeError("on_progress must be callable")
    # Validate external cancellation before any callable is entered.
    with cancellation_scope(cancel_event):
        return _fetch_many(calls, max_workers, on_progress)


def _fetch_many(calls, max_workers, on_progress):
    started = time.monotonic()
    deadline = current_deadline()
    token = CancellationToken()
    names = list(calls)
    results = {}
    stages = {}
    events = queue.SimpleQueue()
    signals = {name: CancellationToken() for name in names}
    interrupted = None
    first_success = None
    nested = in_batch()
    last_progress = [started]

    def emit():
        last_progress[0] = time.monotonic()
        if on_progress is None or interrupted is not None:
            return
        message = {
            "batch_state": "running",
            "completed": len(results),
            "total": len(names),
            "running": {
                name: stages.get(name, "running")
                for name in names
                if name in stages and name not in results
            },
            "pending": [name for name in names if name not in stages and name not in results],
        }
        try:
            on_progress(message)
        except Exception:
            # A broken display callback cannot discard completed business data.
            pass

    def drain():
        changed = False
        while not events.empty():
            name, event = events.get()
            stages[name] = event["stage"]
            changed = True
        if changed:
            emit()

    def interrupt(reason):
        nonlocal interrupted
        if interrupted is None:
            interrupted = reason
            token.cancel(reason)
            for signal in signals.values():
                signal.cancel(reason)

    def check_batch():
        try:
            check_cancelled()
            if deadline is not None and time.monotonic() >= deadline:
                interrupt("timeout")
        except RequestCancelled as exc:
            interrupt(exc.reason)

    def absent(name):
        return {
            "state": "not_started",
            "result": {
                "ok": False,
                "error": interrupted or "cancelled",
                "error_code": "not_started",
            },
            "error": interrupted or "cancelled",
            "timing": {"queue_seconds": round(time.monotonic() - started, 6), "call_seconds": 0.0},
        }

    def execute(name):
        began = time.monotonic()
        entered = False
        result = None
        state = "failed"
        error = None
        depth = _depth.set(_depth.get() + 1)
        with capture_diagnostics(lambda event: events.put((name, event))) as diagnostics:
            try:
                with cancellation_scope(token), cancellation_scope(signals[name]):
                    check_cancelled()
                    if deadline is not None and began >= deadline:
                        raise RequestTimeout("queue_wait")
                    entered = True
                    events.put((name, {"stage": "running"}))
                    validate_arguments(calls[name])
                    result = calls[name]()
                    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                        result = {
                            "ok": False,
                            "error": "invalid_result_envelope",
                            "error_code": "invalid_result_envelope",
                        }
                    state = "success" if result["ok"] else "failed"
                    if state == "failed" and "timeout_stage" in diagnostics:
                        state = "timeout"
            except InvalidArguments as exc:
                error = str(exc)
                result = {"ok": False, "error": error, "error_code": "invalid_arguments"}
            except RequestCancelled as exc:
                error = exc.reason
                state = (
                    ("timeout" if exc.reason == "timeout" else "cancelled")
                    if entered
                    else "not_started"
                )
            except RequestTimeout as exc:
                error = exc.stage
                state = "timeout" if entered else "not_started"
            except KeyboardInterrupt:
                token.cancel("keyboard_interrupt")
                error, state = "keyboard_interrupt", "cancelled"
            except BaseException as exc:
                # Provider envelopes already contain their sanitized diagnostics;
                # arbitrary callable exception text may contain user credentials.
                error = type(exc).__name__
                if "timeout_stage" in diagnostics:
                    state = "timeout"
            finally:
                _depth.reset(depth)
        finished = time.monotonic()
        timing = {key: round(value, 6) for key, value in diagnostics["timing"].items()}
        timing.update(
            queue_seconds=round(began - started, 6), call_seconds=round(finished - began, 6)
        )
        if result is None and state != "success":
            result = {"ok": False, "error": error or state, "error_code": state}
        output = {"state": state, "result": result, "timing": timing}
        if error is not None:
            output["error"] = error
        if diagnostics["events"]:
            output["execution"] = diagnostics["events"]
        return output

    def record(name, result):
        nonlocal first_success
        results[name] = result
        if first_success is None and result["state"] == "success":
            first_success = time.monotonic() - started
        drain()
        emit()

    with cancellation_scope(token):
        try:
            emit()
            if nested:
                for name in names:
                    check_batch()
                    if interrupted is not None:
                        break
                    record(name, copy_context().run(execute, name))
            elif names:
                with ThreadPoolExecutor(
                    max_workers=min(max_workers, len(names)), thread_name_prefix="scutio-fetch"
                ) as pool:
                    pending = {}
                    next_index = 0
                    while pending or next_index < len(names):
                        try:
                            check_batch()
                            while (
                                interrupted is None
                                and len(pending) < max_workers
                                and next_index < len(names)
                            ):
                                name = names[next_index]
                                next_index += 1
                                future = pool.submit(copy_context().run, execute, name)
                                pending[future] = name
                            if not pending:
                                break
                            done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                            drain()
                            if time.monotonic() - last_progress[0] >= 1:
                                emit()
                            for future in done:
                                name = pending[future]
                                record(name, future.result())
                                pending.pop(future)
                        except KeyboardInterrupt:
                            interrupt("keyboard_interrupt")
                            # Continue draining: worker cleanup precedes return.
            check_batch()
        except KeyboardInterrupt:
            interrupt("keyboard_interrupt")
    for name in names:
        if name not in results:
            results[name] = absent(name)
    return {
        "batch_state": "interrupted" if interrupted is not None else "finished",
        "results": {name: results[name] for name in names},
        "timing": {
            "total_seconds": round(time.monotonic() - started, 6),
            "first_success_seconds": round(first_success, 6) if first_success is not None else None,
        },
    }
