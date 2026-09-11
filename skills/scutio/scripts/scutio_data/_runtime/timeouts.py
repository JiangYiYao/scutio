"""Shared elapsed-time budgets for one data operation and its source attempts."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

POLICIES = {"query": (30, 60), "history": (60, 180), "batch": (120, 300), "download": (180, 300)}
_operation = ContextVar("scutio_operation_deadline", default=None)
_source = ContextVar("scutio_source_deadline", default=None)
_kind = ContextVar("scutio_timeout_kind", default="history")
_overrides = ContextVar("scutio_timeout_overrides", default=(None, None))


class RequestTimeout(TimeoutError):
    def __init__(self, stage):
        self.stage = stage
        super().__init__("request timeout: " + stage)


def _seconds(value):
    if isinstance(value, bool):
        raise ValueError("timeout must be a positive finite number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive finite number")
    return value


@contextmanager
def request_timeout(total_seconds, *, source_seconds=None):
    """Override defaults for enclosed calls; nested calls cannot reset a deadline."""
    total = _seconds(total_seconds)
    source = _seconds(source_seconds) if source_seconds is not None else None
    previous = _operation.get()
    token = _operation.set(
        min(previous, time.monotonic() + total) if previous else time.monotonic() + total
    )
    override = _overrides.set((total, source))
    try:
        yield
    finally:
        _overrides.reset(override)
        _operation.reset(token)


def remaining(stage="request", maximum=None):
    deadlines = [value for value in (_operation.get(), _source.get()) if value is not None]
    seconds = min(deadlines) - time.monotonic() if deadlines else POLICIES[_kind.get()][0]
    if maximum is not None:
        seconds = min(seconds, _seconds(maximum))
    if seconds <= 0:
        raise RequestTimeout(stage)
    return seconds


def pause(seconds, stage="retry_wait"):
    if seconds <= 0:
        return
    if seconds >= remaining(stage):
        raise RequestTimeout(stage)
    time.sleep(seconds)


@contextmanager
def operation_budget(kind):
    kind_token = _kind.set(kind)
    token = None
    if _operation.get() is None:
        total = _overrides.get()[0] or POLICIES[kind][1]
        token = _operation.set(time.monotonic() + total)
    try:
        yield
    finally:
        if token is not None:
            _operation.reset(token)
        _kind.reset(kind_token)


def operation(kind):
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
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
