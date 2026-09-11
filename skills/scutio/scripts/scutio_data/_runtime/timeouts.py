"""Shared elapsed-time budgets, cooperative cancellation and execution diagnostics."""

from __future__ import annotations

import inspect
import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from scutio_data._runtime.arguments import validate_arguments

POLICIES = {"query": (30, 60), "history": (60, 180), "batch": (120, 300), "download": (180, 300)}
_operation = ContextVar("scutio_operation_deadline", default=None)
_source = ContextVar("scutio_source_deadline", default=None)
_kind = ContextVar("scutio_timeout_kind", default="history")
_overrides = ContextVar("scutio_timeout_overrides", default=(None, None))
_transport = ContextVar("scutio_transport_overrides", default=(None, None))
_cancellations = ContextVar("scutio_cancellations", default=())
_diagnostics = ContextVar("scutio_execution_diagnostics", default=None)


class RequestTimeout(TimeoutError):
    def __init__(self, stage):
        self.stage = stage
        observe_event("timeout", timeout_stage=stage)
        super().__init__("request timeout: " + stage)


class RequestCancelled(BaseException):
    """Explicit interruption, deliberately not swallowed by provider error fallbacks."""

    stage = "cancelled"

    def __init__(self, reason="cancelled"):
        self.reason = reason
        observe_event("cancelled", reason=reason)
        super().__init__("request cancelled: " + reason)


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()
        self.reason = "cancelled"

    def cancel(self, reason="cancelled"):
        self.reason = reason
        self._event.set()

    def is_set(self):
        return self._event.is_set()


def _event(value):
    if value is not None and not callable(getattr(value, "is_set", None)):
        raise TypeError("cancel_event must expose is_set()")
    return value


@contextmanager
def cancellation_scope(cancel_event):
    event = _event(cancel_event)
    previous = _cancellations.get()
    token = _cancellations.set(previous + (event,)) if event is not None else None
    try:
        yield
    finally:
        if token is not None:
            _cancellations.reset(token)


def check_cancelled():
    for event in _cancellations.get():
        if event.is_set():
            raise RequestCancelled(getattr(event, "reason", "cancelled"))


def current_deadline():
    deadlines = [value for value in (_operation.get(), _source.get()) if value is not None]
    return min(deadlines) if deadlines else None


@contextmanager
def capture_diagnostics(observer=None):
    """Record this call and forward each measurement once to enclosing calls.

    Context copies can share an enclosing record, so its updates are synchronized.
    Observers receive metadata on the originating thread; batch observers enqueue it
    for delivery on their calling thread. Business values never enter this record.
    """
    record = {
        "timing": {},
        "events": [],
        "observer": observer,
        "parent": _diagnostics.get(),
        "lock": threading.Lock(),
    }
    token = _diagnostics.set(record)
    try:
        yield record
    finally:
        _diagnostics.reset(token)


def _diagnostic_records():
    record = _diagnostics.get()
    while record is not None:
        yield record
        record = record["parent"]


def add_timing(name, seconds):
    if isinstance(seconds, (int, float)) and math.isfinite(seconds):
        for record in _diagnostic_records():
            with record["lock"]:
                timing = record["timing"]
                timing[name] = timing.get(name, 0.0) + max(0.0, seconds)


def observe_event(stage, **metadata):
    if _diagnostics.get() is None:
        return
    allowed = {
        "source",
        "provider",
        "endpoint",
        "route",
        "network",
        "attempt",
        "cache_hit",
        "reused",
        "timeout_stage",
        "reason",
        "count",
        "unit",
        "status",
        "state",
    }
    event = {"stage": stage}
    event.update({key: value for key, value in metadata.items() if key in allowed})
    for record in _diagnostic_records():
        with record["lock"]:
            if stage == "timeout":
                record["timeout_stage"] = metadata.get("timeout_stage")
            # Queue polls repeat; actual attempts and cache uses are distinct events.
            if (
                (stage.endswith("_wait") or stage == "provider_queue")
                and record["events"]
                and record["events"][-1] == event
            ):
                continue
            # Keep progress bounded even for a long paginated adapter.
            if len(record["events"]) < 128:
                record["events"].append(dict(event))
            else:
                record["events"][-1] = dict(event)
        observer = record["observer"]
        if observer is not None:
            try:
                observer(dict(event))
            except Exception:
                pass


def _seconds(value):
    if isinstance(value, bool):
        raise ValueError("timeout must be a positive finite number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive finite number")
    return value


@contextmanager
def request_timeout(
    total_seconds,
    *,
    source_seconds=None,
    connect_seconds=None,
    read_seconds=None,
    cancel_event=None,
):
    """Override enclosed calls; every nested deadline remains bounded by its parent."""
    total = _seconds(total_seconds)
    source = _seconds(source_seconds) if source_seconds is not None else None
    previous_transport = _transport.get()
    connect = _seconds(connect_seconds) if connect_seconds is not None else previous_transport[0]
    read = _seconds(read_seconds) if read_seconds is not None else previous_transport[1]
    previous = _operation.get()
    token = _operation.set(
        min(previous, time.monotonic() + total)
        if previous is not None
        else time.monotonic() + total
    )
    override = _overrides.set((total, source if source is not None else _overrides.get()[1]))
    transport = _transport.set((connect, read))
    try:
        with cancellation_scope(cancel_event):
            yield
    finally:
        _transport.reset(transport)
        _overrides.reset(override)
        _operation.reset(token)


def transport_timeout(connect=5, read=20):
    """Explicit transport overrides replace adapter defaults, then respect remaining time."""
    override_connect, override_read = _transport.get()
    return (
        min(
            _seconds(override_connect if override_connect is not None else connect),
            remaining("response"),
        ),
        min(_seconds(override_read if override_read is not None else read), remaining("response")),
    )


def remaining(stage="request", maximum=None):
    check_cancelled()
    deadline = current_deadline()
    seconds = deadline - time.monotonic() if deadline is not None else POLICIES[_kind.get()][0]
    if maximum is not None:
        seconds = min(seconds, _seconds(maximum))
    if seconds <= 0:
        raise RequestTimeout(stage)
    return seconds


def pause(seconds, stage="retry_wait"):
    check_cancelled()
    if seconds <= 0:
        return
    if not math.isfinite(seconds):
        raise ValueError("wait must be finite")
    if seconds >= remaining(stage):
        raise RequestTimeout(stage)
    observe_event(stage)
    started = time.monotonic()
    try:
        # Small bounded sleeps make both native and external threading.Events interruptible.
        until = started + seconds
        while True:
            check_cancelled()
            left = min(until - time.monotonic(), remaining(stage))
            if left <= 0:
                break
            time.sleep(min(0.05, left))
    finally:
        add_timing(stage + "_seconds", time.monotonic() - started)


@contextmanager
def operation_budget(kind):
    kind_token = _kind.set(kind)
    token = None
    if _operation.get() is None:
        total = _overrides.get()[0] or POLICIES[kind][1]
        token = _operation.set(time.monotonic() + total)
    try:
        check_cancelled()
        yield
    finally:
        if token is not None:
            _operation.reset(token)
        _kind.reset(kind_token)


def operation(kind):
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def run(*args, **kwargs):
            validate_arguments(function, args, kwargs, signature=signature)
            with operation_budget(kind):
                return function(*args, **kwargs)

        return run

    return decorate


@contextmanager
def source_budget(kind=None):
    kind = kind or _kind.get()
    token = None
    with operation_budget(kind):
        if _source.get() is None:
            limit = _overrides.get()[1] or POLICIES[kind][0]
            token = _source.set(time.monotonic() + min(limit, remaining()))
        try:
            remaining()
            yield
        finally:
            if token is not None:
                _source.reset(token)


def source(kind):
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            with source_budget(kind):
                return function(*args, **kwargs)

        return run

    return decorate
