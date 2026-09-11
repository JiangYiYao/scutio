"""Bounded API response cache. Documents and coordination state are never evicted."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from scutio_data._runtime.storage import file_lock, private_write, read_json
from scutio_data.paths import cache_dir, state_dir

MAX_BYTES = 128 * 1024 * 1024
SWEEP_INTERVAL = 600
# Imported responses without expiry metadata cannot outlive the longest provider TTL.
MAX_AGE = 21600


def _entries():
    root = cache_dir() / "api"
    if root.is_symlink():
        return []
    paths = []
    for pattern in ("hithink/*/*.json", "akshare/*.json"):
        for path in root.glob(pattern):
            if path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)):
                paths.append(path)
    return paths


def _prune(*, apply: bool, now: float, max_bytes: int):
    entries = []
    for path in _entries():
        try:
            stat = path.stat()
            expiry = read_json(path).get("expires_at", stat.st_mtime + MAX_AGE)
            expired = (
                not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= now
            )
            entries.append((path, stat.st_size, stat.st_mtime, expired))
        except OSError:
            continue
    total = sum(size for _, size, _, _ in entries)
    removed = []
    for path, size, _, expired in sorted(
        entries, key=lambda row: (not row[3], row[2], str(row[0]))
    ):
        if not expired and total <= max_bytes:
            continue
        if apply:
            path.unlink(missing_ok=True)
        removed.append({"path": str(path), "bytes": size})
        total -= size
    return {"applied": apply, "files": removed, "remaining_bytes": total, "max_bytes": max_bytes}


def clean_api_cache(*, apply=False, max_bytes=MAX_BYTES):
    """Preview or remove expired/oldest API entries; lock out concurrent cache writers."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    if not apply:
        return _prune(apply=False, now=time.time(), max_bytes=max_bytes)
    with file_lock(state_dir() / "api_cache.lock"):
        return _prune(apply=True, now=time.time(), max_bytes=max_bytes)


def write_api_cache(path: Path, value: dict, *, ttl: float):
    """Caching is optional: a busy/unwritable cache must not discard fetched data."""
    try:
        with file_lock(state_dir() / "api_cache.lock", timeout_seconds=0):
            now = time.time()
            private_write(path, json.dumps({**value, "expires_at": now + ttl}, ensure_ascii=False))
            sweep_path = state_dir() / "api_cache.json"
            previous = read_json(sweep_path).get("swept_at", 0)
            if not isinstance(previous, (int, float)) or not 0 <= now - previous < SWEEP_INTERVAL:
                _prune(apply=True, now=now, max_bytes=MAX_BYTES)
                private_write(sweep_path, json.dumps({"swept_at": now}))
    except OSError:
        pass
