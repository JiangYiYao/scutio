"""腾讯、新浪和东财直接报价适配；保留源身份、单位和时点。"""

from __future__ import annotations

import requests

from scutio_data._providers import eastmoney
from scutio_data._providers.eastmoney import (
    em_secid_candidates,
    remember_em_secid,
    source_symbol,
)
from scutio_data._providers.quote_parse import (
    amount_pair,
    index_quote_rows,
    parse_sina_quote_raw,
    parse_tencent_quote_raw,
    quote_volume,
)
from scutio_data._runtime.environment import UA
from scutio_data._runtime.http import Session
from scutio_data._runtime.symbols import require_security
from scutio_data._runtime.timeouts import source


def _http_get_bytes(url, *, headers=None, timeout=20, decode=None):
    """Bounded GET; proxy/TLS failures can retry directly within the same budget."""
    hdrs = dict(headers or {})
    if "User-Agent" not in hdrs:
        hdrs["User-Agent"] = UA
    with Session() as session:
        try:
            response = session.get(url, headers=hdrs, timeout=timeout)
        except (requests.exceptions.ProxyError, requests.exceptions.SSLError):
            session.trust_env = False
            response = session.get(url, headers=hdrs, timeout=timeout)
        response.raise_for_status()
        raw = response.content
    if decode:
        return raw.decode(decode)
    return raw


@source("query")
def tencent_quote(codes):
    """腾讯实时报价（A 股 / 港股 / 美股）。"""
    if isinstance(codes, (str, bytes)):
        codes = [codes]
    prefs = [source_symbol(code, "tencent") for code in codes]
    if not prefs:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join(prefs)
    raw = _http_get_bytes(url, timeout=10, decode="gbk")
    return index_quote_rows(parse_tencent_quote_raw(raw))


@source("query")
def sina_quote(codes):
    """新浪实时报价（港/美主用；A 股也可）。"""
    if isinstance(codes, (str, bytes)):
        codes = [codes]
    syms = [source_symbol(code, "sina") for code in codes]
    if not syms:
        return {}
    url = "https://hq.sinajs.cn/list=" + ",".join(syms)
    raw = _http_get_bytes(
        url,
        headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"},
        timeout=10,
        decode="gbk",
    )
    return index_quote_rows(parse_sina_quote_raw(raw))


@source("query")
def eastmoney_quote(codes):
    """东财 push2 单票报价（按 secid；适合港/美 fallback）。"""
    if isinstance(codes, (str, bytes)):
        codes = [codes]
    out = {}
    for code in codes:
        _, prefix, pure = require_security(code, "eastmoney_quote")
        symbol = prefix + pure
        data = {}
        matched_secid = None
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
                or {}
            )
            returned = str(candidate.get("f57") or "")
            if prefix == "us":
                same_ticker = returned.upper().replace("_", ".").replace("-", ".") == pure.replace(
                    "-", "."
                )
            else:
                same_ticker = returned.isdigit() and returned.zfill(len(pure)) == pure
            if candidate and same_ticker:
                data = candidate
                matched_secid = secid
                break
        if not data:
            continue
        if prefix == "us" and matched_secid:
            remember_em_secid(code, matched_secid)
        price = float(data.get("f43") or 0)
        last = float(data.get("f60") or 0)
        chg = float(data.get("f169") or 0)
        pct = float(data.get("f170") or 0)
        currency = "CNY"
        if prefix == "hk":
            currency = "HKD"
        elif prefix == "us":
            currency = "USD"
        # 东财 f48：本币元
        amount, amount_wan = amount_pair(data.get("f48") or 0, raw_unit="yuan")
        row = {
            "name": data.get("f58") or "",
            "price": price,
            "last_close": last,
            "open": float(data.get("f46") or 0),
            "high": float(data.get("f44") or 0),
            "low": float(data.get("f45") or 0),
            **quote_volume(data.get("f47"), "share" if prefix in ("hk", "us") else "lot"),
            "amount": amount,
            "change_amt": chg,
            "change_pct": pct,
            "amount_wan": amount_wan,
            "turnover_pct": (
                float(data["f168"]) if data.get("f168") not in (None, "", "-") else None
            ),
            "pe_ttm": None,
            "amplitude_pct": (
                round((float(data.get("f44")) - float(data.get("f45"))) / last * 100, 4)
                if last
                and data.get("f44") not in (None, "", "-")
                and data.get("f45") not in (None, "", "-")
                else None
            ),
            "mcap_yi": round(float(data.get("f116") or 0) / 1e8, 4)
            if data.get("f116") not in (None, "", "-")
            else None,
            "float_mcap_yi": round(float(data.get("f117") or 0) / 1e8, 4)
            if data.get("f117") not in (None, "", "-")
            else None,
            "pb": None,
            "limit_up": None,
            "limit_down": None,
            "vol_ratio": (float(data["f50"]) if data.get("f50") not in (None, "", "-") else None),
            "pe_static": None,
            "currency": currency,
            "exchange": prefix,
            "symbol": symbol,
            "code": pure,
            "source": "eastmoney",
            "time": "",
        }
        if row["price"] or row["last_close"]:
            out[symbol] = row
    # 裸码仅在无歧义时挂接（与 tencent/sina → index_quote_rows 一致）
    return index_quote_rows(out)
