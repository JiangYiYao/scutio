"""带过期与主源再探的动态数据源偏好。

- 完整成功源记为 ``last_ok``；部分降级不提升为首选源。
- fallback 最多优先一小段时间，随后自动再探主源；偏好本身也有 TTL。
- ``reprobe_context`` 默认只读，诊断不会改生产路由。
- 状态：``$SCUTIO_HOME/state/source_pref.json``；可选偏好读写遇锁忙时跳过，不阻塞取数。

环境变量：
- ``SCUTIO_SOURCE_PREF=0``：关闭，始终默认顺序。
- ``SCUTIO_SOURCE_PREF_PATH``：状态文件路径。
- ``SCUTIO_SOURCE_PREF_REPROBE=1``：强制默认顺序（自检再探主源）。
- ``SCUTIO_SOURCE_PREF_TTL``：偏好有效秒数（默认 3600）。
- ``SCUTIO_SOURCE_PREF_PRIMARY_PROBE_INTERVAL``：fallback 后主源再探间隔（默认 300 秒）。
- ``SCUTIO_SOURCE_PREF_FAIL_COOLDOWN``：失败源冷却秒数（默认 60）。"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from scutio_data._runtime.storage import file_lock
from scutio_data.paths import atomic_write_text, state_dir

__all__ = [
    "ordered_sources",
    "mark_ok",
    "mark_fail",
    "get_pref",
    "clear_pref",
    "pref_enabled",
    "reprobe_context",
    "reset_runtime_state",
]


_lock = threading.RLock()


_state: Optional[Dict[str, Any]] = None


_state_mtime_ns: Optional[int] = None


_state_path: Optional[str] = None


def pref_enabled() -> bool:
    raw = os.environ.get("SCUTIO_SOURCE_PREF", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _reprobe_mode() -> bool:
    raw = os.environ.get("SCUTIO_SOURCE_PREF_REPROBE", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _writes_enabled() -> bool:
    raw = os.environ.get("SCUTIO_SOURCE_PREF_READ_ONLY", "0").strip().lower()
    return raw not in ("1", "true", "yes", "on")


def _seconds_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


@contextmanager
def reprobe_context(*, apply: bool = False) -> Iterator[None]:
    """按默认顺序探测；默认只读，``apply=True`` 才更新偏好。"""
    prev = os.environ.get("SCUTIO_SOURCE_PREF_REPROBE")
    prev_read_only = os.environ.get("SCUTIO_SOURCE_PREF_READ_ONLY")
    os.environ["SCUTIO_SOURCE_PREF_REPROBE"] = "1"
    if not apply:
        os.environ["SCUTIO_SOURCE_PREF_READ_ONLY"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("SCUTIO_SOURCE_PREF_REPROBE", None)
        else:
            os.environ["SCUTIO_SOURCE_PREF_REPROBE"] = prev
        if prev_read_only is None:
            os.environ.pop("SCUTIO_SOURCE_PREF_READ_ONLY", None)
        else:
            os.environ["SCUTIO_SOURCE_PREF_READ_ONLY"] = prev_read_only


def _path() -> Path:
    override = os.environ.get("SCUTIO_SOURCE_PREF_PATH")
    if override:
        return Path(override).expanduser()
    return state_dir() / "source_pref.json"


@contextmanager
def _state_access(*, write=False) -> Iterator[bool]:
    """Optional routing state never waits for a thread or another process."""
    if not _lock.acquire(blocking=False):
        yield False
        return
    try:
        with ExitStack() as stack:
            if write:
                path = _path()
                try:
                    stack.enter_context(
                        file_lock(path.with_suffix(path.suffix + ".lock"), timeout_seconds=0)
                    )
                except (ImportError, OSError):
                    yield False
                    return
            yield True
    finally:
        _lock.release()


def _load(*, force: bool = False) -> Dict[str, Any]:
    global _state, _state_mtime_ns, _state_path
    with _lock:
        path = _path()
        path_key = str(path)
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = None
        if (
            not force
            and _state is not None
            and _state_path == path_key
            and _state_mtime_ns == mtime_ns
        ):
            return _state
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            _state = data if isinstance(data, dict) else {}
        else:
            _state = {}
        if "caps" not in _state or not isinstance(_state.get("caps"), dict):
            _state["caps"] = {}
        _state_path = path_key
        _state_mtime_ns = mtime_ns
        return _state


def _save() -> None:
    global _state_mtime_ns, _state_path
    with _lock:
        path = _path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                path,
                json.dumps(_state or {"caps": {}}, ensure_ascii=False, indent=2),
            )
            _state_path = str(path)
            _state_mtime_ns = path.stat().st_mtime_ns
        except Exception:
            pass


def get_pref(capability: str) -> Dict[str, Any]:
    with _state_access() as acquired:
        if not acquired:
            return {}
        caps = _load().get("caps") or {}
        return dict(caps.get(capability) or {})


def clear_pref(capability: Optional[str] = None) -> None:
    with _state_access(write=True) as acquired:
        if not acquired:
            raise TimeoutError("source preference state is busy or unwritable; retry clear")
        st = _load(force=True)
        if capability is None:
            st["caps"] = {}
        else:
            st.setdefault("caps", {}).pop(capability, None)
        _save()


def reset_runtime_state() -> None:
    """丢弃内存缓存（测试改 ``SCUTIO_SOURCE_PREF_PATH`` 后必须调用，否则仍读旧状态）。"""
    global _state, _state_mtime_ns, _state_path
    with _state_access() as acquired:
        if not acquired:
            raise TimeoutError("source preference state is busy; retry reset")
        _state = None
        _state_mtime_ns = None
        _state_path = None


def ordered_sources(capability: str, default_order: Sequence[str]) -> List[str]:
    """完整成功源短期优先；到期或达到再探间隔时恢复默认主源顺序。"""
    default = [s for s in default_order if s]
    if not default:
        return []
    if not pref_enabled() or _reprobe_mode():
        return list(default)

    info = get_pref(capability)
    last_ok = info.get("last_ok")
    if last_ok and last_ok in default:
        now = time.time()
        last_ok_at = float(info.get("last_ok_at") or 0.0)
        ttl = _seconds_env("SCUTIO_SOURCE_PREF_TTL", 3600.0)
        if ttl and now - last_ok_at >= ttl:
            return list(default)
        primary = info.get("default_primary") or default[0]
        if last_ok != primary and primary in default:
            interval = _seconds_env("SCUTIO_SOURCE_PREF_PRIMARY_PROBE_INTERVAL", 300.0)
            probe_at = float(
                info.get("primary_probe_at") or info.get("fallback_since") or last_ok_at
            )
            if interval == 0 or now - probe_at >= interval:
                if _writes_enabled():
                    with _state_access(write=True) as acquired:
                        if acquired:
                            st = _load(force=True)
                            current = st.setdefault("caps", {}).setdefault(capability, {})
                            current["primary_probe_at"] = now
                            _save()
                return list(default)
        return [last_ok] + [s for s in default if s != last_ok]
    now = time.time()
    cooldown = _seconds_env("SCUTIO_SOURCE_PREF_FAIL_COOLDOWN", 60.0)
    fails = info.get("fails") or {}
    cooling = {
        source
        for source in default
        if source in fails
        and cooldown
        and now - float((fails.get(source) or {}).get("at") or 0.0) < cooldown
    }
    if cooling and len(cooling) < len(default):
        return [s for s in default if s not in cooling] + [s for s in default if s in cooling]
    return list(default)


def mark_ok(
    capability: str,
    source: str,
    *,
    default_primary: Optional[str] = None,
    quality: str = "complete",
) -> None:
    if not pref_enabled() or not _writes_enabled() or not source:
        return
    with _state_access(write=True) as acquired:
        if not acquired:
            return
        st = _load(force=True)
        caps = st.setdefault("caps", {})
        previous = dict(caps.get(capability) or {})
        if quality != "complete":
            previous["last_partial_ok"] = source
            previous["last_partial_ok_at"] = time.time()
            if default_primary:
                previous["default_primary"] = default_primary
            caps[capability] = previous
            _save()
            return
        now = time.time()
        info: Dict[str, Any] = {
            "last_ok": source,
            "last_ok_at": now,
            "fails": dict(previous.get("fails") or {}),
        }
        info["fails"].pop(source, None)
        if default_primary:
            info["default_primary"] = default_primary
        elif previous.get("default_primary"):
            info["default_primary"] = previous["default_primary"]
        primary = info.get("default_primary")
        if primary and source != primary:
            info["fallback_since"] = previous.get("fallback_since") or now
            if previous.get("primary_probe_at"):
                info["primary_probe_at"] = previous["primary_probe_at"]
        caps[capability] = info
        _save()


def mark_fail(capability: str, source: str, *, default_primary: Optional[str] = None) -> None:
    """记录失败；不改 last_ok。"""
    if not pref_enabled() or not _writes_enabled() or not source:
        return
    with _state_access(write=True) as acquired:
        if not acquired:
            return
        st = _load(force=True)
        caps = st.setdefault("caps", {})
        prev_info = dict(caps.get(capability) or {})
        fails = dict(prev_info.get("fails") or {})
        prev = fails.get(source) or {}
        fails[source] = {
            "at": time.time(),
            "count": int(prev.get("count") or 0) + 1,
        }
        info: Dict[str, Any] = {"fails": fails}
        if "last_ok" in prev_info:
            info["last_ok"] = prev_info["last_ok"]
        if "last_ok_at" in prev_info:
            info["last_ok_at"] = prev_info["last_ok_at"]
        primary = default_primary or prev_info.get("default_primary")
        if primary:
            info["default_primary"] = primary
        caps[capability] = info
        _save()
