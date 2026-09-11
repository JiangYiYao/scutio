"""东财符号映射、共享限流和直接请求传输。"""

import errno
import os
import random
import threading
import time

import requests

from scutio_data._runtime.environment import UA, _env_flag
from scutio_data._runtime.http import Session, retry_after
from scutio_data._runtime.symbols import split_code
from scutio_data._runtime.timeouts import RequestTimeout, pause, remaining, source

EM_SESSION = Session()


_EM_RECOVERY_SESSION = Session()


_EM_DIRECT_SESSION = Session()


EM_MIN_INTERVAL = 1.0


EM_MAX_ATTEMPTS = 2


EM_BACKOFF_FACTOR = 0.6


_EM_TRANSIENT_STATUS = frozenset((429, 500, 502, 503, 504))


_em_last_start = [0.0]


_em_rate_lock = threading.Lock()


_em_us_market_lock = threading.Lock()


_em_us_market_cache = {}


_EM_US_MARKET_IDS = ("105", "106", "107")


def source_symbol(code, source):
    """把代码映射为指定上游的请求符号。

    ``source``：
      - ``tencent`` / ``display`` → ``hk00700`` / ``usAAPL`` / ``sh600519``
      - ``sina`` → 新浪 list 符号（港 ``rt_hk00700``，美 ``gb_aapl``，A ``sh600519``）
      - ``eastmoney_secid`` / ``em_secid`` → ``116.00700`` / ``105.AAPL`` / ``1.600519``
    """
    prefix, pure = split_code(code)
    src = str(source or "").strip().lower()
    if src in ("tencent", "display", "canonical"):
        return prefix + pure
    if src in ("eastmoney_secid", "em_secid", "secid"):
        return em_secid(code)
    if src == "sina":
        if prefix == "hk":
            return "rt_hk" + pure
        if prefix == "us":
            # Sina uses '$' for class shares: BRK.B → gb_brk$b.
            slug = pure.lower().replace(".", "$").replace("-", "$")
            return "gb_" + slug
        return prefix + pure
    raise ValueError("unknown source_symbol source: %r" % (source,))


def em_secid(code):
    """东财股票 ``secid``。

    A 股：沪 ``1.`` / 深北 ``0.``；港股 ``116.``；美股返回已解析缓存，
    未解析时兼容返回 ``105.``。美股业务请求应使用 :func:`em_secid_candidates`
    并在成功后调用 :func:`remember_em_secid`。
    指数与深市代码重叠时请显式 ``sh`` 前缀。
    """
    prefix, pure = split_code(code)
    if prefix == "hk":
        return "116.%s" % pure
    if prefix == "us":
        with _em_us_market_lock:
            market_id = _em_us_market_cache.get(pure.upper(), "105")
        return "%s.%s" % (market_id, pure)
    market = 1 if prefix == "sh" else 0
    return "%s.%s" % (market, pure)


def em_secid_candidates(code):
    """返回东财 ``secid`` 候选；美股覆盖 NASDAQ/NYSE/AMEX 并优先已命中缓存。"""
    prefix, pure = split_code(code)
    if prefix != "us":
        return [em_secid(code)]
    ticker = pure.upper()
    with _em_us_market_lock:
        cached = _em_us_market_cache.get(ticker)
    market_ids = ([cached] if cached else []) + [
        item for item in _EM_US_MARKET_IDS if item != cached
    ]
    return ["%s.%s" % (market_id, pure) for market_id in market_ids]


def remember_em_secid(code, secid):
    """记住已验证的美股东财 market id；非美股或非法 ``secid`` 不处理。"""
    prefix, pure = split_code(code)
    if prefix != "us":
        return
    market_id = str(secid or "").split(".", 1)[0]
    if market_id not in _EM_US_MARKET_IDS:
        return
    with _em_us_market_lock:
        _em_us_market_cache[pure.upper()] = market_id


def em_src_security_code(code):
    """东财 app ``srcSecurityCode``（如 ``SH600519``）。A 股专用。"""
    prefix, pure = split_code(code)
    if prefix in ("hk", "us"):
        raise ValueError("Eastmoney srcSecurityCode does not support %s code: %r" % (prefix, code))
    return prefix.upper() + pure


def _em_should_retry_without_proxy(exc):
    """是否像是坏代理导致的失败。"""
    if isinstance(exc, requests.exceptions.ProxyError):
        return True
    text = str(exc).lower()
    return "proxy" in text or "cannot connect to proxy" in text


def _em_record_reserved_start(handle):
    """Wait for the shared slot and persist its start time while holding a file lock."""
    handle.seek(0)
    try:
        last_start = float(handle.read().strip() or 0.0)
    except ValueError:
        last_start = 0.0
    wait = EM_MIN_INTERVAL - (time.time() - last_start)
    if wait > 0:
        pause(wait + random.uniform(0.1, 0.5), "rate_wait")
    started = time.time()
    handle.seek(0)
    handle.truncate()
    handle.write("%.9f" % started)
    handle.flush()


def _em_reserve_start_windows(path):
    """Reserve a cross-process slot with the Windows CRT byte-range lock."""
    import msvcrt

    with open(path, "a+", encoding="utf-8") as handle:
        # ``msvcrt.locking`` locks bytes from the current file position.  Make
        # sure byte zero exists before competing for it; concurrent creators
        # may both write the same sentinel without changing its semantics.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
            handle.flush()
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                pause(0.05, "queue_wait")
        try:
            _em_record_reserved_start(handle)
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _em_reserve_start_posix(path):
    """Reserve a cross-process slot with a POSIX advisory file lock."""
    import fcntl

    with open(path, "a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                pause(0.05, "queue_wait")
        try:
            _em_record_reserved_start(handle)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _em_reserve_start():
    """预约下一次东财请求开始时间；默认同时约束当前进程和同机其他进程。"""
    if _env_flag("SCUTIO_EM_CROSS_PROCESS_RATE", "1"):
        try:
            from scutio_data.paths import state_dir

            state_path = os.environ.get("SCUTIO_EM_RATE_STATE_PATH")
            path = (
                os.path.expanduser(state_path)
                if state_path
                else str(state_dir() / "em_rate_limit.state")
            )
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if os.name == "nt":
                _em_reserve_start_windows(path)
            else:
                _em_reserve_start_posix(path)
            return
        except RequestTimeout:
            raise
        except (ImportError, OSError):
            # Unsupported/read-only filesystems retain safe process-local
            # throttling; normal Windows and POSIX hosts use the shared file.
            pass
    if not _em_rate_lock.acquire(timeout=remaining("queue_wait")):
        raise RequestTimeout("queue_wait")
    try:
        now = time.monotonic()
        wait = EM_MIN_INTERVAL - (now - _em_last_start[0])
        if wait > 0:
            pause(wait + random.uniform(0.1, 0.5), "rate_wait")
        _em_last_start[0] = time.monotonic()
    finally:
        _em_rate_lock.release()


def _em_request(session, method, url, params, headers, timeout, kwargs):
    remaining("rate_wait")
    _em_reserve_start()
    return session.request(method, url, params=params, headers=headers, timeout=timeout, **kwargs)


@source(None)
def em_get(url, params=None, headers=None, timeout=20, method="GET", **kwargs):
    """东财统一传输入口；每次真实尝试均先限流，瞬时错误最多尝试两次。

    坏环境代理会切到 ``trust_env=False`` 的直连 Session；响应残缺仅用无重试
    Session 恢复一次。HTTPAdapter 本身不重试，避免绕过限流或形成重试乘积。
    """
    proxies_locked = "proxies" in kwargs
    last_exc = None
    direct_mode = False
    delay = EM_BACKOFF_FACTOR
    for attempt in range(EM_MAX_ATTEMPTS):
        if attempt:
            pause(delay)
        try:
            response = _em_request(
                _EM_DIRECT_SESSION if direct_mode else EM_SESSION,
                method,
                url,
                params,
                headers,
                timeout,
                kwargs,
            )
            status = getattr(response, "status_code", None)
            if status in _EM_TRANSIENT_STATUS and attempt + 1 < EM_MAX_ATTEMPTS:
                if status == 429 or response.headers.get("Retry-After"):
                    delay = retry_after(response.headers.get("Retry-After"))
                continue
            return response
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if not proxies_locked and not direct_mode and _em_should_retry_without_proxy(exc):
                direct_mode = True
                if attempt + 1 >= EM_MAX_ATTEMPTS:
                    raise
                continue
            if (
                isinstance(exc, requests.exceptions.ChunkedEncodingError)
                and attempt + 1 < EM_MAX_ATTEMPTS
            ):
                return _em_request(
                    _EM_RECOVERY_SESSION,
                    method,
                    url,
                    params,
                    headers,
                    timeout,
                    kwargs,
                )
            if not isinstance(
                exc,
                (requests.exceptions.ConnectionError, requests.exceptions.Timeout),
            ):
                raise
            if attempt + 1 >= EM_MAX_ATTEMPTS:
                raise
    if last_exc is not None:
        raise last_exc
    raise requests.exceptions.RequestException("eastmoney request failed")


for _session in (EM_SESSION, _EM_RECOVERY_SESSION, _EM_DIRECT_SESSION):
    _session.headers.update({"User-Agent": UA})
    _session.trust_env = _env_flag("SCUTIO_TRUST_ENV", "1")

try:
    from requests.adapters import HTTPAdapter

    # 重试必须由 em_get 协调，才能保证每一次真实请求都经过限流器。
    for _session in (EM_SESSION, _EM_RECOVERY_SESSION, _EM_DIRECT_SESSION):
        _session.mount("https://", HTTPAdapter(max_retries=0))
        _session.mount("http://", HTTPAdapter(max_retries=0))
except Exception:
    pass

_EM_DIRECT_SESSION.trust_env = False
