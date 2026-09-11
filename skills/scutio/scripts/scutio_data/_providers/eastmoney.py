"""Eastmoney symbol mapping; transport policy belongs to the shared HTTP executor."""

import threading

from scutio_data._runtime.environment import UA, _env_flag
from scutio_data._runtime.http import Session
from scutio_data._runtime.symbols import split_code
from scutio_data._runtime.timeouts import source

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


@source(None)
def em_get(url, params=None, headers=None, timeout=20, method="GET", **kwargs):
    """One isolated session; shared execution owns quota, recovery and limited retry."""
    with Session() as session:
        session.headers.update({"User-Agent": UA})
        session.trust_env = _env_flag("SCUTIO_TRUST_ENV", "1")
        return session.request(
            method,
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            _source_attempts=2,
            **kwargs,
        )
