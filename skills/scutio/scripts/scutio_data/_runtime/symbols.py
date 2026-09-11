"""证券标识归一化与领域市场边界；不执行网络请求。"""

import re


def _normalize_us_ticker(ticker, original):
    """Validate and normalize a US ticker (uppercase; allow class shares like BRK.B)."""
    t = str(ticker or "").strip().upper().replace(" ", "")
    if t.startswith("US."):
        t = t[3:]
    if not t or t.isdigit():
        raise ValueError("invalid US ticker: %r" % (original,))
    # Letters first; allow digits/dots/hyphens (BRK.B, BF.B, RDS-A style).
    if not t[0].isalpha():
        raise ValueError("invalid US ticker: %r" % (original,))
    for ch in t:
        if not (ch.isalnum() or ch in ".-"):
            raise ValueError("invalid US ticker: %r" % (original,))
    if len(t) > 12:
        raise ValueError("invalid US ticker: %r" % (original,))
    return t


def split_code(code):
    """拆成 ``(交易所前缀, 代码)``。

    显式标记优先：``sh000001``、``000001.SH``、``hk02513``、``02513.HK``、
    ``usAAPL``、``AAPL.US`` 等。
    无标记时按 A 股个股启发式（``5/6/9``→沪，北交所形态→bj，其余→sz）。
    沪市指数与深市个股代码重叠时必须带 ``sh``。
    港股返回 5 位码；裸 4–5 位数字不会推断为港股。
    美股必须显式 ``us`` / ``.US``；裸 ticker（如 ``AAPL``）拒绝。
    """
    raw = str(code).strip()
    if not raw:
        raise ValueError("empty security code")
    upper = raw.upper()

    # Strip optional r_ / R_ quote prefix used by some feeds.
    if upper.startswith("R_"):
        upper = upper[2:]
    if not upper or any(ch.isspace() for ch in upper):
        raise ValueError("invalid security code: %r" % (code,))

    # --- United States (must be before digit-only A-share path) ---
    match = re.fullmatch(r"([A-Z][A-Z0-9.\-]{0,11})\.US", upper)
    if match:
        return "us", _normalize_us_ticker(match.group(1), code)
    match = re.fullmatch(r"US\.([A-Z][A-Z0-9.\-]{0,11})", upper)
    if match:
        return "us", _normalize_us_ticker(match.group(1), code)
    match = re.fullmatch(r"US([A-Z][A-Z0-9.\-]{0,11})", upper)
    if match:
        return "us", _normalize_us_ticker(match.group(1), code)

    # --- Hong Kong ---
    match = re.fullmatch(r"(\d{1,5})\.HK", upper)
    if not match:
        match = re.fullmatch(r"HK\.?([0-9]{1,5})", upper)
    if match:
        return "hk", match.group(1).zfill(5)
    if upper.endswith(".HK") or upper.startswith("HK"):
        raise ValueError("invalid HK code: %r" % (code,))

    # --- Explicit A share ---
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", upper)
    if match:
        return match.group(2).lower(), match.group(1)
    match = re.fullmatch(r"(SH|SZ|BJ)\.?([0-9]{6})", upper)
    if match:
        return match.group(1).lower(), match.group(2)
    if upper.endswith((".SH", ".SZ", ".BJ")) or upper.startswith(("SH", "SZ", "BJ")):
        raise ValueError("invalid A-share code: %r" % (code,))

    if re.fullmatch(r"\d{6}", upper):
        code6 = upper
    elif re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", upper):
        raise ValueError(
            "US ticker %r requires explicit prefix us/US or suffix .US "
            "(e.g. usAAPL / AAPL.US); bare tickers are not inferred" % (code,)
        )
    else:
        raise ValueError(
            "invalid security code: %r; pass a security code, not a company name "
            "(e.g. 600519 / hk00700 / usAAPL)" % (code,)
        )

    # Infer exchange when caller omitted it (stock-oriented defaults).
    if code6.startswith(("110", "113", "204")):
        return "sh", code6
    if code6.startswith(("43", "83", "87", "92")) or code6.startswith("8"):
        return "bj", code6
    if code6.startswith(("5", "6", "9")):
        return "sh", code6
    return "sz", code6


def normalize_code(code):
    """去掉交易所前缀，返回纯代码（A/港数字码或美股 ticker）。"""
    return split_code(code)[1]


def get_prefix(code):
    """返回小写交易所前缀（sh/sz/bj/hk/us）。"""
    return split_code(code)[0]


def market_of(code):
    """市场族：``a`` / ``hk`` / ``us``。"""
    prefix = get_prefix(code)
    if prefix in ("hk", "us"):
        return prefix
    return "a"


def require_market(code, allowed, capability="capability"):
    """在发起网络请求前校验能力支持的市场，返回 ``(market, prefix, pure)``。"""
    prefix, pure = split_code(code)
    market = market_of(code)
    if not allowed:
        raise ValueError("market policy missing for capability %r" % capability)
    allowed_set = {str(item).lower() for item in allowed}
    if market not in allowed_set:
        raise ValueError(
            "unsupported_market: %s supports %s; got %s code %r"
            % (capability, "/".join(sorted(allowed_set)), market, code)
        )
    return market, prefix, pure


def canonical_symbol(code):
    """规范展示符号：``sh600519`` / ``hk00700`` / ``usAAPL``。"""
    prefix, pure = split_code(code)
    return prefix + pure


_A_COMPANY_PREFIXES = {
    "sh": ("600", "601", "603", "605", "688", "689"),
    "sz": ("000", "001", "002", "003", "300", "301"),
    "bj": ("43", "83", "87", "88", "92"),
}


def security_kind(code):
    """Known mainland code families; this is not a live listing directory."""
    prefix, pure = split_code(code)
    if prefix in ("hk", "us"):
        # Overseas tickers alone do not establish whether the issuer is a fund.
        return "overseas"
    if pure.startswith(_A_COMPANY_PREFIXES[prefix]):
        return "company"
    if (
        prefix == "sh"
        and pure.startswith(("000", "880"))
        or prefix == "sz"
        and pure.startswith("399")
        or prefix == "bj"
        and pure.startswith("899")
    ):
        return "index"
    if prefix == "sh" and pure.startswith("5") or prefix == "sz" and pure.startswith(("15", "16")):
        return "fund"
    return "unsupported"


def require_security(code, capability="security", allowed=("a", "hk", "us")):
    """Reject unsupported or exchange-mismatched mainland quote/bar identities."""
    identity = require_market(code, allowed, capability)
    if security_kind(code) == "unsupported":
        raise ValueError("unsupported_asset: %s does not support this code/exchange" % capability)
    return identity


def is_a_share(code):
    """Whether the complete code denotes a supported mainland company."""
    return security_kind(code) == "company"


def require_company(code, capability, allowed=("a", "hk", "us")):
    """Keep company-only mainland endpoints from querying an overlapping bare code."""
    identity = require_market(code, allowed, capability)
    if identity[0] == "a" and not is_a_share(code):
        raise ValueError("unsupported_asset: %s requires an A-share company" % capability)
    return identity


def require_a_share(code, capability):
    return require_company(code, capability, allowed={"a"})


def validate_identity(record, code, *, fields=("symbol", "stockCode", "code"), required=True):
    """Check every identity claim; only known mainland company bare codes are unambiguous."""
    if not isinstance(record, dict):
        raise ValueError("security identity must be an object")
    expected = canonical_symbol(code)
    prefix, pure = split_code(code)
    claims = []
    exchange = record.get("exchange")
    for field in fields:
        value = record.get(field)
        if value in (None, ""):
            continue
        text = str(value)
        if text == pure:
            if exchange:
                claim = canonical_symbol(str(exchange) + text)
            elif is_a_share(code) and is_a_share(text):
                claim = canonical_symbol(text)
            else:
                # A bare ticker may accompany an explicit symbol, but is not proof alone.
                continue
        else:
            claim = canonical_symbol(text)
        claims.append(claim)
    if any(claim != expected for claim in claims):
        raise ValueError("security identity mismatch")
    if exchange and str(exchange).lower() != prefix:
        raise ValueError("security identity mismatch")
    # Even a bare code accompanying a correct full symbol must not contradict it.
    for field in fields:
        value = record.get(field)
        if value not in (None, "") and str(value).isdigit() and str(value) != pure:
            raise ValueError("security identity mismatch")
    if required and not claims:
        raise ValueError("security identity missing")
    return bool(claims)


def validate_report_identity(envelope, code):
    """Validate reused report envelopes and every row before computing a comparison."""
    try:
        known = validate_identity(envelope, code, required=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("report envelope identity mismatch") from exc
    rows = envelope.get("items")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("report items must be a list of objects")
    if not rows and not known:
        raise ValueError("report envelope identity missing")
    for row in rows:
        try:
            validate_identity(row, code)
        except ValueError as exc:
            raise ValueError(str(exc).replace("security", "report", 1)) from exc
    return rows
