"""Cancellable local and cross-process locks for source requests.

Locks keep their inode for the lifetime of the state directory. Network work
holds a slot, while short state transactions reserve a request start time.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import weakref
from contextlib import ExitStack, contextmanager
from pathlib import Path

from scutio_data._runtime.storage import file_lock, private_write, read_json
from scutio_data._runtime.timeouts import (
    check_cancelled,
    pause,
    remaining,
)

_locks = weakref.WeakValueDictionary()
_guard = threading.Lock()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@contextmanager
def try_lock(path):
    """Try both a thread lock and an OS lock without waiting."""
    path = Path(path)
    key = str(path.absolute())
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
    if not lock.acquire(blocking=False):
        yield False
        return
    stack = ExitStack()
    try:
        try:
            stack.enter_context(file_lock(path, timeout_seconds=0))
            acquired = True
        except TimeoutError:
            acquired = False
        yield acquired
    finally:
        stack.close()
        lock.release()


def wait(seconds, stage):
    check_cancelled()
    pause(min(seconds, 0.05, remaining(stage)), stage)


@contextmanager
def locked(path, *, stage="coordination_wait"):
    while True:
        remaining(stage)
        with try_lock(path) as acquired:
            if acquired:
                yield
                return
        wait(0.02, stage)


def update_json(path, update, *, required=True):
    """Update shared state under a short lock; optional observations never wait."""
    path = Path(path)
    if required:
        with locked(path.with_suffix(".lock")):
            value = read_json(path)
            result = update(value)
            private_write(path, json.dumps(value, ensure_ascii=False))
            return result
    try:
        with try_lock(path.with_suffix(".lock")) as acquired:
            if acquired:
                value = read_json(path)
                result = update(value)
                private_write(path, json.dumps(value, ensure_ascii=False))
                return result
    except OSError:
        pass
    return None


def _slot(stack, root, count):
    for index in range(count):
        candidate = ExitStack()
        if candidate.enter_context(try_lock(root / (str(index) + ".lock"))):
            stack.enter_context(candidate)
            return True
        candidate.close()
    return False


@contextmanager
def permit(root, *, max_inflight, interval=0.0, family=None, family_limit=None):
    """Acquire a network slot only when a request is also eligible to start.

    Rate waits do not retain a slot. Recheck the shared start clock after slots
    are acquired so processes waking together cannot create a burst.
    """
    root = Path(root)
    while True:
        remaining("provider_queue")
        delay, stage = 0.02, "provider_queue"
        with ExitStack() as stack:
            family_ready = family_limit is None or _slot(
                stack, root / "families" / digest(family), family_limit
            )
            if family_ready and _slot(stack, root / "slots", max_inflight):
                rate_path = root / "rate.json"
                # Do not retain slots while another process holds the state lock.
                rate_lock = ExitStack()
                acquired = rate_lock.enter_context(try_lock(root / "rate.lock"))
                try:
                    if acquired:
                        now = time.time()
                        previous = read_json(rate_path).get("last_start", 0)
                        if (
                            not isinstance(previous, (int, float))
                            or not math.isfinite(previous)
                            or previous > now + interval
                        ):
                            previous = 0
                        delay = max(0.0, previous + interval - now)
                        stage = "rate_wait"
                        if delay == 0:
                            private_write(rate_path, json.dumps({"last_start": now}))
                    else:
                        delay = 0.02
                finally:
                    rate_lock.close()
                if acquired and delay == 0:
                    remaining("response")
                    yield
                    return
        wait(delay, stage)
