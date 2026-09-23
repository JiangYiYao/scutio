"""腾讯、新浪和东财直接报价适配；保留源身份、单位和时点。"""

from __future__ import annotations

from scutio_data._providers import eastmoney
from scutio_data._providers.eastmoney import (
    em_secid_candidates,
    remember_em_secid,
    source_symbol,
)
from scutio_data._providers.quote_parse import (
    index_quote_rows,
    parse_eastmoney_quote,
    parse_sina_quote_raw,
    parse_tencent_quote_raw,
)
from scutio_data._runtime.environment import UA
from scutio_data._runtime.http import Session
from scutio_data._runtime.symbols import require_security
from scutio_data._runtime.timeouts import RequestTimeout, remaining, source

QUOTE_BATCH_SIZE = 100


def _http_get_bytes(url, *, headers=None, timeout=20, decode=None):
    """Bounded GET; proxy/TLS failures can retry directly within the same budget."""
    hdrs = dict(headers or {})
    if "User-Agent" not in hdrs:
        hdrs["User-Agent"] = UA
    with Session() as session:
        response = session.get(url, headers=hdrs, timeout=timeout)
        response.raise_for_status()
        raw = response.content
    if decode:
        return raw.decode(decode)
    return raw


def _batched_quote(codes, provider, url_prefix, parser, *, headers=None):
    """Bound each URL and retain completed rows if another batch fails."""
    if isinstance(codes, (str, bytes)):
        codes = [codes]
    symbols = list(dict.fromkeys(source_symbol(code, provider) for code in codes))
    out = {}
    failures = []
    for start in range(0, len(symbols), QUOTE_BATCH_SIZE):
        try:
            remaining()
            raw = _http_get_bytes(
                url_prefix + ",".join(symbols[start : start + QUOTE_BATCH_SIZE]),
                headers=headers,
                timeout=10,
                decode="gbk",
            )
            out.update(parser(raw))
        except RequestTimeout as exc:
            failures.append(exc)
            break
        except Exception as exc:
            failures.append(exc)
    if not out and failures:
        raise failures[0]
    # Index aliases only after all batches are merged: collisions may straddle chunks.
    return index_quote_rows(out)


@source("query")
def tencent_quote(codes):
    """腾讯实时报价（A 股 / 港股 / 美股）；每请求最多 100 只。"""
    return _batched_quote(codes, "tencent", "https://qt.gtimg.cn/q=", parse_tencent_quote_raw)


@source("query")
def sina_quote(codes):
    """新浪实时报价（港/美主用；A 股也可）。"""
    return _batched_quote(
        codes,
        "sina",
        "https://hq.sinajs.cn/list=",
        parse_sina_quote_raw,
        headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"},
    )


@source("query")
def eastmoney_quote(codes):
    """东财 push2 单票报价（按 secid；适合港/美 fallback）。"""
    if isinstance(codes, (str, bytes)):
        codes = [codes]
    out = {}
    failures = []
    for code in codes:
        try:
            remaining()
            row = _eastmoney_quote_one(code)
        except RequestTimeout as exc:
            failures.append(exc)
            break
        except Exception as exc:
            # This source requests securities separately. Keep completed rows when
            # another response fails; the facade can retry the missing symbol.
            failures.append(exc)
            continue
        if row is not None:
            out[row["symbol"]] = row
    if not out and failures:
        raise failures[0]
    return index_quote_rows(out)


def _eastmoney_quote_one(code):
    """Resolve and parse one security independently from its batch peers."""
    _, prefix, pure = require_security(code, "eastmoney_quote")
    for secid in em_secid_candidates(code):
        candidate = (
            eastmoney.em_get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "fltt": "2",
                    "invt": "2",
                    "fields": "f57,f58,f43,f44,f45,f46,f47,f48,f50,f60,f169,f170,f168,f116,f117",
                    "secid": secid,
                },
                headers={"Referer": "https://quote.eastmoney.com/"},
                timeout=15,
            )
            .json()
            .get("data")
        )
        if candidate is None:
            continue
        if not isinstance(candidate, dict):
            raise ValueError("invalid Eastmoney quote payload")
        returned = str(candidate.get("f57") or "")
        if prefix == "us":
            same_ticker = returned.upper().replace("_", ".").replace("-", ".") == pure.replace(
                "-", "."
            )
        else:
            same_ticker = returned.isdigit() and returned.zfill(len(pure)) == pure
        if not same_ticker:
            continue
        row = parse_eastmoney_quote(candidate, prefix=prefix, pure=pure)
        if row is not None:
            if prefix == "us":
                remember_em_secid(code, secid)
            return row
    return None
