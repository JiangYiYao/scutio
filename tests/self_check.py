"""取数通路真实环境自检（位于 tests/；运维/开发用，非 scutio_data 公开 API）。

对关键取数通路做**联网探测 + 返回结构校验**，区分：

- ``ok``：通路通且结构符合契约
- ``degraded``：主源失败但备胎/降级源可用，或字段部分缺失仍可用
- ``fail``：通路失败或结构崩坏（疑似上游改版/封禁）
- ``skip``：未启用该探针

CLI（仓库根）::

    export PYTHONPATH=skills/scutio/scripts
    "$SCUTIO_PYTHON" tests/self_check.py
    "$SCUTIO_PYTHON" tests/self_check.py --group market,capital
    "$SCUTIO_PYTHON" tests/self_check.py --json -o /tmp/scutio_health.json
    "$SCUTIO_PYTHON" tests/self_check.py --list
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")

__all__ = [
    "ProbeResult",
    "SelfCheckReport",
    "run_self_check",
    "render_markdown",
    "render_json",
    "list_probes",
    "PROBES",
    "COVERAGE_GAPS",
]

# 代表性标的（流动性好，结构应稳定）
CODE_SH = "600519"
CODE_SZ = "000001"
CODE_IDX = "sh000001"
CODE_ETF = "510300"


# ── 结构校验小工具 ─────────────────────────────────────────────


def _price_ok(v: Any, lo: float = 0.0, hi: float = 1e7) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return lo < x < hi


def _has_keys(obj: Any, keys: Sequence[str]) -> Tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "not a dict"
    missing = [k for k in keys if k not in obj]
    if missing:
        return False, "missing keys: %s" % ",".join(missing)
    return True, ""


def _is_data_result_ok(obj: Any) -> Tuple[bool, str]:
    """统一 dict 信封：``ok`` 必须为 True（列表型含 ``items``）。"""
    if isinstance(obj, dict):
        if obj.get("ok") is False:
            return False, "envelope ok=False error=%r" % obj.get("error")
        if "ok" not in obj:
            return False, "dict missing ok"
        return True, ""
    if isinstance(obj, (list, tuple)):
        return True, ""
    return False, "not envelope dict/list"


def _rows(obj: Any) -> List[Any]:
    """从 list 信封取 ``items``；已是 list 则原样。"""
    if isinstance(obj, dict) and "items" in obj:
        return list(obj.get("items") or [])
    if isinstance(obj, (list, tuple)):
        return list(obj)
    return []


def _brief(obj: Any, limit: int = 280) -> str:
    try:
        if isinstance(obj, dict) and "ok" in obj:
            items = obj.get("items")
            n = len(items) if isinstance(items, list) else "?"
            s = "envelope(ok=%s, source=%r, n=%s, error=%r)" % (
                obj.get("ok"),
                obj.get("source"),
                n,
                obj.get("error"),
            )
            if isinstance(items, list) and items:
                s += " first=%r" % (items[0],)
            text = s
        elif isinstance(obj, dict):
            keys = list(obj.keys())[:12]
            slim = {k: obj[k] for k in keys}
            text = repr(slim)
        else:
            text = repr(obj)
    except Exception as exc:  # noqa: BLE001
        text = "<brief_error %s>" % exc
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass
class ProbeResult:
    id: str
    name: str
    group: str
    entry: str
    status: str  # ok | degraded | fail | skip
    critical: bool
    elapsed_ms: float
    message: str
    structure_ok: bool
    sample: str = ""
    error: Optional[str] = None
    checks: List[str] = field(default_factory=list)


@dataclass
class SelfCheckReport:
    generated_at: str
    code: str
    total: int
    ok: int
    degraded: int
    fail: int
    skip: int
    critical_fail: int
    results: List[ProbeResult]
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Probe:
    id: str
    name: str
    group: str
    entry: str
    critical: bool
    run: Callable[[], Tuple[str, bool, str, str, List[str]]]
    # run → (status, structure_ok, message, sample, checks)
    # status in ok|degraded|fail


def _probe_tencent_quote() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.market import tencent_quote

    checks: List[str] = []
    batch = tencent_quote([CODE_SH, CODE_SZ, CODE_IDX])
    if not isinstance(batch, dict) or not batch:
        return "fail", False, "empty or non-dict quote map", _brief(batch), checks
    checks.append("dict_non_empty")
    row = batch.get("sh" + CODE_SH) or batch.get(CODE_SH)
    if not row:
        return "fail", False, "missing 600519 row keys=%s" % list(batch)[:8], _brief(batch), checks
    for k in ("name", "price"):
        if k not in row:
            return "fail", False, "quote row missing %s" % k, _brief(row), checks
        checks.append("has_%s" % k)
    if not row.get("name"):
        return "fail", False, "name empty (structure/layout drift?)", _brief(row), checks
    if not _price_ok(row.get("price")):
        return "fail", False, "price invalid %r" % row.get("price"), _brief(row), checks
    checks.append("price_range")
    # 可选字段漂移 → degraded
    optional = [k for k in ("pe_ttm", "pb", "mcap_yi") if k not in row]
    status = "ok"
    msg = "tencent quote ok"
    if optional:
        status = "degraded"
        msg = "core ok; optional missing: %s" % ",".join(optional)
        checks.append("optional_missing")
    return status, True, msg, _brief(row), checks


def _probe_security_quote() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.market import security_quote

    checks: List[str] = []
    out = security_quote([CODE_SH, CODE_SZ])
    ok_keys, err = _has_keys(out, ("ok", "quotes", "sources_used"))
    if not ok_keys:
        return "fail", False, err, _brief(out), checks
    checks.append("envelope_keys")
    if not out.get("ok"):
        return "fail", False, "ok=false error=%r" % out.get("error"), _brief(out), checks
    quotes = out.get("quotes") or {}
    if not quotes:
        return "fail", False, "quotes empty", _brief(out), checks
    checks.append("quotes_non_empty")
    sample = next(iter(quotes.values()))
    if not isinstance(sample, dict) or not (
        _price_ok(sample.get("price")) or _price_ok(sample.get("last_close"))
    ):
        return "fail", False, "no priced row", _brief(sample), checks
    checks.append("priced_row")
    return (
        "ok",
        True,
        "sources=%s" % out.get("sources_used"),
        _brief({"sources_used": out.get("sources_used"), "sample": sample}),
        checks,
    )


def _probe_security_bars() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.market import security_bars

    checks: List[str] = []
    out = security_bars(CODE_SH, frequency="D", count=5)
    ok_keys, err = _has_keys(out, ("ok", "bars", "source"))
    if not ok_keys:
        return "fail", False, err, _brief(out), checks
    checks.append("envelope_keys")
    if not out.get("ok"):
        return "fail", False, "ok=false %r" % out.get("error"), _brief(out), checks
    bars = out.get("bars") or []
    if len(bars) < 1:
        return "fail", False, "empty bars", _brief(out), checks
    last = bars[-1]
    for k in ("close", "open", "high", "low"):
        if k not in last:
            return "fail", False, "bar missing %s (layout drift?)" % k, _brief(last), checks
        checks.append("bar_%s" % k)
    if not _price_ok(last.get("close")):
        return "fail", False, "close invalid", _brief(last), checks
    return (
        "ok",
        True,
        "source=%s n=%d" % (out.get("source"), len(bars)),
        _brief(last),
        checks,
    )


def _probe_security_bars_index() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.market import security_bars

    checks: List[str] = []
    out = security_bars(CODE_IDX, frequency="D", count=3, index=True)
    if not out.get("ok"):
        return "fail", False, "ok=false %r" % out.get("error"), _brief(out), checks
    bars = out.get("bars") or []
    if not bars or not _price_ok(bars[-1].get("close")):
        return "fail", False, "no valid index bar", _brief(out), checks
    checks.append("index_bar")
    return "ok", True, "source=%s" % out.get("source"), _brief(bars[-1]), checks


def _probe_eastmoney_stock_info() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.fundamentals import stock_info

    checks: List[str] = []
    info = stock_info(CODE_SH)
    if not isinstance(info, dict):
        return "fail", False, "not dict", _brief(info), checks
    src = info.get("source")
    if src in (None, "empty"):
        return "fail", False, "source empty", _brief(info), checks
    checks.append("source=%s" % src)
    if src == "eastmoney":
        if not info.get("name"):
            return "fail", False, "eastmoney name empty (field drift?)", _brief(info), checks
        checks.append("name")
        if not (_price_ok(info.get("price")) or float(info.get("mcap") or 0) > 0):
            return "degraded", True, "name ok but price/mcap weak", _brief(info), checks
        return "ok", True, "eastmoney stock_info", _brief(info), checks
    # quote fallback
    return "degraded", True, "using fallback source=%s" % src, _brief(info), checks


def _probe_valuation_snapshot() -> Tuple[str, bool, str, str, List[str]]:
    """报价侧估值快照（不含一致预期）。"""
    from scutio_data.valuation import valuation_snapshot

    checks: List[str] = []
    out = valuation_snapshot(CODE_SH)
    ok_keys, err = _has_keys(out, ("ok", "error", "source"))
    if not ok_keys:
        return "fail", False, err, _brief(out), checks
    if not out.get("ok"):
        return "fail", False, "ok=false %r" % out.get("error"), _brief(out), checks
    if not _price_ok(out.get("price")) or not out.get("name"):
        return "fail", False, "price/name missing", _brief(out), checks
    checks.append("price_name")
    if out.get("pe_ttm") is not None or out.get("pb") is not None:
        checks.append("pe_or_pb")
    return "ok", True, "quote-side valuation ok", _brief(out), checks


def _probe_cninfo_announcements() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.announcements import stock_announcements

    checks: List[str] = []
    rows = stock_announcements(CODE_SH, page_size=5)
    ok, err = _is_data_result_ok(rows)
    if not ok:
        return "fail", False, err, _brief(rows), checks
    rows = _rows(rows)
    if len(rows) < 1:
        return "fail", False, "empty list (org id / API change?)", _brief(rows), checks
    first = rows[0]
    if not isinstance(first, dict):
        return "fail", False, "item not dict", _brief(first), checks
    for k in ("title", "url"):
        if not first.get(k):
            return "fail", False, "item missing %s" % k, _brief(first), checks
        checks.append(k)
    return "ok", True, "n=%d" % len(rows), _brief(first), checks


def _probe_eastmoney_stock_news() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.feeds import stock_news

    checks: List[str] = []
    rows = stock_news(CODE_SH, page_size=5)
    ok, err = _is_data_result_ok(rows)
    if not ok:
        return "fail", False, err, _brief(rows), checks
    rows = _rows(rows)
    checks.append("ok")
    if len(rows) == 0:
        return "degraded", True, "ok but empty (search API may have changed)", _brief(rows), checks
    if not rows[0].get("title"):
        return "fail", False, "title empty (field drift)", _brief(rows[0]), checks
    checks.append("title")
    return "ok", True, "n=%d" % len(rows), _brief(rows[0]), checks


def _probe_cls_telegraph() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.feeds import telegraph

    checks: List[str] = []
    rows = telegraph(page_size=5)
    ok, err = _is_data_result_ok(rows)
    if not ok:
        return "fail", False, err, _brief(rows), checks
    rows = _rows(rows)
    if len(rows) < 1:
        return "fail", False, "empty telegraph", _brief(rows), checks
    item = rows[0]
    if not (item.get("title") or item.get("content")):
        return "fail", False, "title/content empty", _brief(item), checks
    checks.append("content")
    return "ok", True, "n=%d" % len(rows), _brief(item), checks


def _probe_fund_flow_120d() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.capital.flows import stock_fund_flow_120d

    checks: List[str] = []
    rows = stock_fund_flow_120d(CODE_SH)
    ok, err = _is_data_result_ok(rows)
    rows = _rows(rows)
    if ok and len(rows) >= 1:
        sample = rows[-1]
        if not (sample.get("date") or sample.get("time")):
            return "fail", False, "row missing date/time", _brief(sample), checks
        checks.append("primary")
        return "ok", True, "primary n=%d" % len(rows), _brief(sample), checks
    return "fail", False, "AKShare daily fund flow unavailable: %s" % err, _brief(rows), checks


def _probe_concept_blocks() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data._providers.eastmoney_capital import concept_blocks

    checks: List[str] = []
    env = concept_blocks(CODE_SH)
    if not isinstance(env, dict) or not env.get("ok"):
        return "fail", False, "ok=false %r" % env.get("error"), _brief(env), checks
    boards = env.get("boards") or []
    checks.append("envelope")
    if not boards:
        return "degraded", True, "empty concept boards", _brief(env), checks
    if not boards[0].get("name"):
        return "fail", False, "board row missing name", _brief(boards[0]), checks
    checks.append("name")
    return "ok", True, "n=%d" % len(boards), _brief(boards[0]), checks


def _probe_industry_comparison() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.capital import industry_comparison

    checks: List[str] = []
    out = industry_comparison(top_n=5)
    if not isinstance(out, dict):
        return "fail", False, "not dict", _brief(out), checks
    top = out.get("top") or []
    bottom = out.get("bottom") or []
    if top or bottom:
        checks.append("has_leg")
        # 行结构
        sample = (top or bottom)[0]
        if isinstance(sample, dict) and not any(
            k in sample for k in ("name", "code", "pct", "f3", "change_pct")
        ):
            return "degraded", True, "rows present but unexpected keys", _brief(sample), checks
        src = out.get("source") or ""
        if src == "sina_industry":
            checks.append("sina_fallback")
            return (
                "degraded",
                True,
                "sina fallback ok=%s top=%d bottom=%d" % (out.get("ok"), len(top), len(bottom)),
                _brief({"top0": top[0] if top else None, "bottom0": bottom[0] if bottom else None}),
                checks,
            )
        status = "ok" if out.get("ok") else "degraded"
        return (
            status,
            True,
            "ok=%s source=%s top=%d bottom=%d" % (out.get("ok"), src, len(top), len(bottom)),
            _brief({"top0": top[0] if top else None, "bottom0": bottom[0] if bottom else None}),
            checks,
        )
    return "fail", False, "both legs empty error=%r" % out.get("error"), _brief(out), checks


def _probe_local_report_search() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.research.local import local_report_search

    checks: List[str] = []
    env = local_report_search("人工智能", begin="2025-01-01", max_pages=1, limit=5)
    if not isinstance(env, dict):
        return "fail", False, "not dict envelope", _brief(env), checks
    if env.get("ok") is False:
        return "fail", False, "ok=false %r" % env.get("error"), _brief(env), checks
    checks.append("envelope")
    results = env.get("items") or []
    if not results:
        return "degraded", True, "empty (upstream list empty or rate limit)", "", checks
    r0 = results[0]
    if not isinstance(r0, dict) or not (r0.get("title") or r0.get("infoCode")):
        return "fail", False, "record shape drift", _brief(r0), checks
    checks.append("title_or_infoCode")
    return "ok", True, "n=%d" % len(results), _brief(r0), checks


def _probe_sina_financial() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.fundamentals import financial_report

    checks: List[str] = []
    data = financial_report(CODE_SH, report_type="lrb", num=4)
    # 可能是 dict 信封 / list — 兼容
    if data is None:
        return "fail", False, "None", "", checks
    if isinstance(data, dict):
        if data.get("ok") is False:
            return "fail", False, "ok=false %r" % data.get("error"), _brief(data), checks
        # 常见：嵌套 report 或 items
        if not data and "error" not in str(data):
            return "degraded", True, "empty dict", _brief(data), checks
        checks.append("dict")
        return "ok", True, "sina lrb dict keys=%s" % list(data.keys())[:8], _brief(data), checks
    if hasattr(data, "ok"):
        ok, err = _is_data_result_ok(data)
        if not ok:
            return "fail", False, err, _brief(data), checks
        data = _rows(data)
        if len(data) < 1:
            return "degraded", True, "ok empty", _brief(data), checks
        return "ok", True, "n=%d" % len(data), _brief(data[0] if data else data), checks
    if isinstance(data, (list, tuple)):
        if len(data) < 1:
            return "degraded", True, "empty list", "", checks
        return "ok", True, "list n=%d" % len(data), _brief(data[0]), checks
    return "degraded", True, "unexpected type %s" % type(data).__name__, _brief(data), checks


def _probe_split_code_local() -> Tuple[str, bool, str, str, List[str]]:
    """不联网：符号解析自检（结构契约的本地底座）。"""
    from scutio_data._providers.eastmoney import em_secid
    from scutio_data._runtime.symbols import split_code

    checks: List[str] = []
    cases = [
        ("sh000001", ("sh", "000001")),
        ("600519", ("sh", "600519")),
        ("000001", ("sz", "000001")),
        ("hk00700", ("hk", "00700")),
        ("usAAPL", ("us", "AAPL")),
        ("AAPL.US", ("us", "AAPL")),
    ]
    for raw, expect in cases:
        got = split_code(raw)
        if got != expect:
            return "fail", False, "split_code(%r)=%r expect %r" % (raw, got, expect), "", checks
        checks.append(raw)
    if em_secid("510050") != "1.510050":
        return "fail", False, "em_secid 510050 drift", "", checks
    checks.append("em_secid")
    if em_secid("hk00700") != "116.00700":
        return "fail", False, "em_secid HK drift", "", checks
    checks.append("em_secid_hk")
    if em_secid("usAAPL") != "105.AAPL":
        return "fail", False, "em_secid US drift", "", checks
    checks.append("em_secid_us")
    try:
        split_code("AAPL")
        return "fail", False, "bare US ticker AAPL should raise", "", checks
    except ValueError:
        checks.append("bare_us_rejected")
    return "ok", True, "local symbol helpers ok (A/HK/US)", "", checks


def _probe_hk_us_quote() -> Tuple[str, bool, str, str, List[str]]:
    """港/美多源报价。"""
    from scutio_data.market import security_quote

    checks: List[str] = []
    out = security_quote(["hk00700", "usAAPL"])
    ok_keys, err = _has_keys(out, ("ok", "quotes", "sources_used"))
    if not ok_keys:
        return "fail", False, err, _brief(out), checks
    checks.append("envelope_keys")
    if not out.get("ok"):
        return "fail", False, "ok=false error=%r" % out.get("error"), _brief(out), checks
    quotes = out.get("quotes") or {}
    hk = quotes.get("hk00700") or quotes.get("00700")
    us = quotes.get("usAAPL") or quotes.get("AAPL")
    if not hk or not _price_ok(hk.get("price")):
        return "fail", False, "hk00700 missing/invalid price", _brief(out), checks
    checks.append("hk_price")
    if not us or not _price_ok(us.get("price")):
        return "fail", False, "usAAPL missing/invalid price", _brief(out), checks
    checks.append("us_price")
    return (
        "ok",
        True,
        "sources=%s hk=%s us=%s" % (out.get("sources_used"), hk.get("price"), us.get("price")),
        _brief({"hk": hk, "us": us, "sources_used": out.get("sources_used")}),
        checks,
    )


def _probe_hk_us_bars() -> Tuple[str, bool, str, str, List[str]]:
    """港/美日 K（默认不复权）。"""
    from scutio_data.market import security_bars

    checks: List[str] = []
    for code in ("hk00700", "usAAPL"):
        out = security_bars(code, frequency="D", count=5)
        ok_keys, err = _has_keys(out, ("ok", "bars", "source"))
        if not ok_keys:
            return "fail", False, "%s %s" % (code, err), _brief(out), checks
        if not out.get("ok") or not (out.get("bars") or []):
            return (
                "fail",
                False,
                "%s bars fail error=%r" % (code, out.get("error")),
                _brief(out),
                checks,
            )
        last = (out.get("bars") or [])[-1]
        for k in ("open", "high", "low", "close"):
            if k not in last:
                return "fail", False, "%s bar missing %s" % (code, k), _brief(last), checks
        checks.append("%s_ok" % code)
        checks.append("src_%s=%s" % (code, out.get("source")))
    return "ok", True, "hk+us bars ok", "", checks


def _probe_hk_us_stock_info() -> Tuple[str, bool, str, str, List[str]]:
    """港/美东财个股资料。"""
    from scutio_data.fundamentals import stock_info

    checks: List[str] = []
    samples = []
    for code in ("hk00700", "usAAPL"):
        info = stock_info(code)
        if not isinstance(info, dict):
            return "fail", False, "%s non-dict" % code, _brief(info), checks
        if not info.get("name") and info.get("source") == "empty":
            return (
                "degraded",
                True,
                "%s empty stock_info (EM down?)" % code,
                _brief(info),
                checks,
            )
        if not info.get("name"):
            return (
                "fail",
                False,
                "%s name empty source=%s" % (code, info.get("source")),
                _brief(info),
                checks,
            )
        checks.append("%s_name" % code)
        samples.append(info)
    return "ok", True, "hk+us stock_info ok", _brief(samples[0] if samples else {}), checks


def _recent_trade_dates(n: int = 10) -> List[str]:
    """最近 n 个日历日（含周末；龙虎接口无数据日会空列表）。"""
    out = []
    now = datetime.now(CST)
    for i in range(n):
        out.append((now - timedelta(days=i)).strftime("%Y-%m-%d"))
    return out


def _probe_daily_dragon_tiger() -> Tuple[str, bool, str, str, List[str]]:
    """全市场龙虎榜：找最近有数据的交易日；区分「接口挂」与「当日无榜」。"""
    from scutio_data.capital import daily_dragon_tiger

    checks: List[str] = []
    last_err = None
    empty_days = 0
    for day in _recent_trade_dates(12):
        out = daily_dragon_tiger(trade_date=day)
        if not isinstance(out, dict):
            return "fail", False, "not dict", _brief(out), checks
        if not out.get("ok"):
            last_err = out.get("error")
            # 明确错误 → 失败；继续试其他日可能被同一错误挡住
            continue
        checks.append("envelope_ok")
        for k in ("date", "stocks", "total_records"):
            if k not in out:
                return "fail", False, "missing key %s" % k, _brief(out), checks
            checks.append(k)
        stocks = out.get("stocks") or []
        if stocks:
            row = stocks[0]
            for k in ("code", "name", "net_buy_wan"):
                if k not in row:
                    return (
                        "fail",
                        False,
                        "stock row missing %s (layout drift)" % k,
                        _brief(row),
                        checks,
                    )
                checks.append("row_%s" % k)
            return (
                "ok",
                True,
                "date=%s n=%d" % (out.get("date"), len(stocks)),
                _brief(row),
                checks,
            )
        empty_days += 1
    if last_err:
        return (
            "fail",
            False,
            "ok=false last_error=%r empty_days=%d" % (last_err, empty_days),
            "",
            checks,
        )
    # 12 天全空但都 ok：少见，多半接口变了返回空
    return (
        "degraded",
        True,
        "API ok but no board rows in last 12 calendar days (holiday/filter?)",
        "",
        checks,
    )


def _probe_dragon_tiger_board() -> Tuple[str, bool, str, str, List[str]]:
    """个股龙虎：用全市场榜找一只当日上榜股，再拉席位；无榜则用茅台宽窗口看信封。"""
    from scutio_data.capital import daily_dragon_tiger, dragon_tiger_board

    checks: List[str] = []
    code = CODE_SH
    trade_date = datetime.now(CST).strftime("%Y-%m-%d")
    # 尽量找真实上榜标的
    for day in _recent_trade_dates(12):
        market = daily_dragon_tiger(trade_date=day)
        if isinstance(market, dict) and market.get("ok") and (market.get("stocks") or []):
            code = str(market["stocks"][0].get("code") or CODE_SH)
            trade_date = day
            checks.append("picked_from_daily")
            break

    out = dragon_tiger_board(code, trade_date, look_back=30)
    if not isinstance(out, dict):
        return "fail", False, "not dict", _brief(out), checks
    for k in ("ok", "records", "seats", "institution"):
        if k not in out:
            return "fail", False, "missing %s" % k, _brief(out), checks
        checks.append(k)
    if not out.get("ok"):
        return "fail", False, "ok=false %r" % out.get("error"), _brief(out), checks
    seats = out.get("seats") or {}
    if not isinstance(seats, dict) or "buy" not in seats or "sell" not in seats:
        return "fail", False, "seats shape drift", _brief(seats), checks
    checks.append("seats_shape")
    records = out.get("records") or []
    if not records:
        # 信封通、该股窗口内未上榜 — 不算断
        return (
            "degraded",
            True,
            "envelope ok; no records for %s@%s (not on board)" % (code, trade_date),
            _brief(out),
            checks,
        )
    r0 = records[0]
    if "date" not in r0 or "net_buy" not in r0:
        return "fail", False, "record field drift", _brief(r0), checks
    checks.append("record_fields")
    return (
        "ok",
        True,
        "code=%s date=%s records=%d" % (code, trade_date, len(records)),
        _brief({"record0": r0, "buy_seats": len(seats.get("buy") or [])}),
        checks,
    )


def _probe_lockup_expiry() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.announcements import lockup_expiry

    checks: List[str] = []
    trade_date = datetime.now(CST).strftime("%Y-%m-%d")
    last_out = None
    # 多标的：部分大票解禁表为空不代表接口挂
    for code in (CODE_SZ, CODE_SH, "000858"):
        out = lockup_expiry(code, trade_date, forward_days=365)
        last_out = out
        if not isinstance(out, dict):
            return "fail", False, "not dict", _brief(out), checks
        for k in ("ok", "history", "upcoming"):
            if k not in out:
                return "fail", False, "missing %s" % k, _brief(out), checks
        if not out.get("ok"):
            return (
                "fail",
                False,
                "ok=false %r code=%s" % (out.get("error"), code),
                _brief(out),
                checks,
            )
        hist = out.get("history") or []
        if hist:
            h0 = hist[0]
            for k in ("date", "shares", "type"):
                if k not in h0:
                    return "fail", False, "history row missing %s" % k, _brief(h0), checks
                checks.append("hist_%s" % k)
            checks.append("code=%s" % code)
            return (
                "ok",
                True,
                "code=%s history=%d upcoming=%d"
                % (code, len(hist), len(out.get("upcoming") or [])),
                _brief(h0),
                checks,
            )
    checks.append("envelope_only")
    return (
        "degraded",
        True,
        "ok envelopes but empty history on sample codes",
        _brief(last_out),
        checks,
    )


def _probe_cninfo_irm() -> Tuple[str, bool, str, str, List[str]]:
    """互动易：优先用深市活跃样本（平安银行），茅台等可能长期无问答。"""
    from scutio_data.announcements import irm

    checks: List[str] = []
    # 000001 互动更活跃；600519 常为空列表（合法但易误判为通路问题）
    sample = CODE_SZ
    rows = irm(sample, page_size=5)
    ok, err = _is_data_result_ok(rows)
    if not ok:
        return "fail", False, err, _brief(rows), checks
    rows = _rows(rows)
    checks.append("ok")
    if len(rows) < 1:
        # 再试一票，避免单票无问答造成假降级
        alt = "000858"
        rows2 = irm(alt, page_size=5)
        ok2, err2 = _is_data_result_ok(rows2)
        if not ok2:
            return "fail", False, err2, _brief(rows2), checks
        rows2 = _rows(rows2)
        if len(rows2) >= 1:
            sample, rows = alt, rows2
        else:
            return (
                "degraded",
                True,
                "ok empty IRM list (tried %s,%s)" % (CODE_SZ, alt),
                _brief(rows),
                checks,
            )
    item = rows[0]
    if not isinstance(item, dict):
        return "fail", False, "item not dict", _brief(item), checks
    # 互动易字段名可能 question/answer/title
    if not any(item.get(k) for k in ("question", "answer", "title", "content")):
        return (
            "fail",
            False,
            "IRM fields empty/drift keys=%s" % list(item.keys())[:8],
            _brief(item),
            checks,
        )
    checks.append("content_field")
    return "ok", True, "code=%s n=%d" % (sample, len(rows)), _brief(item), checks


def _probe_southbound_daily() -> Tuple[str, bool, str, str, List[str]]:
    """Validate daily southbound data without substituting quota or minute data."""
    from scutio_data.capital import southbound_daily

    env = southbound_daily(page_size=10)
    ok, err = _is_data_result_ok(env)
    rows = _rows(env)
    if ok and rows:
        return "ok", True, "southbound n=%d" % len(rows), _brief(rows[0]), ["southbound_daily"]
    return "fail", False, "southbound=%s" % (err or "empty data"), _brief(env), []


def _probe_margin_trading() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.capital import margin_trading

    checks: List[str] = []
    rows = margin_trading(CODE_SH, page_size=5)
    ok, err = _is_data_result_ok(rows)
    if not ok:
        return "fail", False, err, _brief(rows), checks
    rows = _rows(rows)
    if len(rows) < 1:
        return "degraded", True, "ok empty margin history", _brief(rows), checks
    r0 = rows[0]
    if not isinstance(r0, dict):
        return "fail", False, "row not dict", _brief(r0), checks
    checks.append("rows")
    return "ok", True, "n=%d" % len(rows), _brief(r0), checks


def _probe_eastmoney_reports() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.research import stock_reports

    checks: List[str] = []
    rows = stock_reports(CODE_SH, max_pages=1, begin="2024-01-01")
    if rows is None:
        return "fail", False, "None", "", checks
    if isinstance(rows, dict) and rows.get("ok") is False:
        return "fail", False, "ok=false %r" % rows.get("error"), _brief(rows), checks
    if isinstance(rows, dict) and "items" in rows:
        rows = rows.get("items") or []
    seq = list(rows)
    if len(seq) < 1:
        return "degraded", True, "empty report list", "", checks
    r0 = seq[0]
    if not isinstance(r0, dict) or not (
        r0.get("title") or r0.get("infoCode") or r0.get("reportTitle")
    ):
        return "fail", False, "report shape drift", _brief(r0), checks
    checks.append("title")
    return "ok", True, "n=%d" % len(seq), _brief(r0), checks


def _probe_macro_lpr() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.macro import lpr_history

    checks: List[str] = []
    rows = lpr_history(limit=3)
    ok, err = _is_data_result_ok(rows)
    if not ok:
        return "fail", False, err, _brief(rows), checks
    rows = _rows(rows)
    if len(rows) < 1:
        return "fail", False, "empty lpr", _brief(rows), checks
    r0 = rows[0]
    if r0.get("lpr_1y") is None and r0.get("lpr_5y") is None:
        return "fail", False, "lpr values null", _brief(r0), checks
    checks.append("lpr_rate")
    return "ok", True, "date=%s lpr1y=%s" % (r0.get("date"), r0.get("lpr_1y")), _brief(r0), checks


def _probe_macro_cpi() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.macro.series import cn_macro_series

    checks: List[str] = []
    rows = cn_macro_series("cpi_yoy", limit=3)
    ok, err = _is_data_result_ok(rows)
    if not ok:
        return "fail", False, err, _brief(rows), checks
    rows = _rows(rows)
    if not rows or rows[0].get("value") is None:
        return "degraded", True, "cpi empty or null value", _brief(rows), checks
    checks.append("cpi_yoy")
    return (
        "ok",
        True,
        "date=%s value=%s" % (rows[0].get("date"), rows[0].get("value")),
        _brief(rows[0]),
        checks,
    )


def _probe_macro_fx_or_bonds() -> Tuple[str, bool, str, str, List[str]]:
    """汇率或中美债至少一腿成功即可。"""
    from scutio_data.macro import bond_yields_cn_us
    from scutio_data.macro.quotes import fx_usdcny

    checks: List[str] = []
    fx = fx_usdcny()
    if fx.get("ok") and _price_ok(fx.get("price"), lo=1.0, hi=20.0):
        checks.append("fx_usdcny")
        return "ok", True, "USDCNY=%s" % fx.get("price"), _brief(fx), checks
    bonds = bond_yields_cn_us(limit=2)
    ok, err = _is_data_result_ok(bonds)
    from scutio_data._runtime.results import envelope_items

    bitems = envelope_items(bonds) if isinstance(bonds, dict) else list(bonds or [])
    if (
        ok
        and bitems
        and (bitems[0].get("cn_10y") is not None or bitems[0].get("us_10y") is not None)
    ):
        checks.append("bonds")
        return (
            "ok",
            True,
            "cn10y=%s us10y=%s" % (bitems[0].get("cn_10y"), bitems[0].get("us_10y")),
            _brief(bitems[0]),
            checks,
        )
    return (
        "fail",
        False,
        "fx_err=%r bonds_err=%r"
        % (
            fx.get("error"),
            err if not ok else (bonds.get("error") if isinstance(bonds, dict) else None),
        ),
        "",
        checks,
    )


def _probe_global_news() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.feeds import global_news

    checks: List[str] = []
    rows = global_news(page_size=5)
    ok, err = _is_data_result_ok(rows)
    if not ok:
        return "fail", False, err, _brief(rows), checks
    rows = _rows(rows)
    if len(rows) < 1:
        return "degraded", True, "ok empty global news", _brief(rows), checks
    if not rows[0].get("title") and not rows[0].get("content"):
        return "fail", False, "title/content empty", _brief(rows[0]), checks
    checks.append("title")
    return "ok", True, "n=%d" % len(rows), _brief(rows[0]), checks


def _probe_periodic_reports_a() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.announcements import periodic_reports

    checks: List[str] = []
    env = periodic_reports(CODE_SH, kind="annual", page_size=3)
    ok, err = _is_data_result_ok(env)
    if not ok:
        return "fail", False, err, _brief(env), checks
    checks.append("ok")
    rows = _rows(env)
    if not rows:
        return "degraded", True, "ok empty annual list", _brief(env), checks
    first = rows[0]
    if not first.get("title") and not first.get("pdf_url"):
        return "fail", False, "missing title/pdf_url", _brief(first), checks
    checks.append("item")
    return "ok", True, "n=%d" % len(rows), _brief(first), checks


def _probe_periodic_reports_hk() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.announcements import periodic_reports

    checks: List[str] = []
    env = periodic_reports("hk00700", kind="annual", page_size=2)
    ok, err = _is_data_result_ok(env)
    if not ok:
        return "fail", False, err, _brief(env), checks
    checks.append("ok")
    if not env.get("fallback_hint"):
        return "degraded", True, "missing fallback_hint", _brief(env), checks
    checks.append("fallback_hint")
    rows = _rows(env)
    if not rows:
        return "degraded", True, "ok empty HK annual (filter sparse?)", _brief(env), checks
    checks.append("n=%d" % len(rows))
    return "ok", True, "n=%d" % len(rows), _brief(rows[0]), checks


def _probe_periodic_reports_us() -> Tuple[str, bool, str, str, List[str]]:
    from scutio_data.announcements import periodic_reports

    checks: List[str] = []
    env = periodic_reports("usAAPL", kind="annual", page_size=2)
    ok, err = _is_data_result_ok(env)
    if not ok:
        return "fail", False, err, _brief(env), checks
    checks.append("ok")
    if not env.get("fallback_hint"):
        return "degraded", True, "missing fallback_hint", _brief(env), checks
    checks.append("fallback_hint")
    rows = _rows(env)
    if not rows:
        return "degraded", True, "ok empty US 10-K in SEC recent", _brief(env), checks
    first = rows[0]
    if not (first.get("file_url") or first.get("pdf_url")):
        return "fail", False, "missing file_url", _brief(first), checks
    checks.append("file_url")
    return (
        "ok",
        True,
        "n=%d form=%s" % (len(rows), first.get("type")),
        _brief(first),
        checks,
    )


# 仍未单独探针、但属模块能力的清单（报告脚注；纯本地/纯计算/已被组合探针覆盖的不列入）
COVERAGE_GAPS: List[str] = [
    "market: QUOTE/BARS 非默认 source 链细项（已覆盖 security_* 默认链；港美已覆盖 hk_us_* 探针）",
    "market: 港/美 Level-2、逐笔、盘前盘后精细字段、美股 class share 全覆盖",
    "fundamentals: stock_materials（个股资料长文，仅 A；财务快照仅 stock_info 内部降级）",
    "fundamentals: 港/美完整三表字段深度 — 披露原文见 announcements.periodic_reports 探针",
    "research: industry_reports/broker_reports、report_page_detail、download_pdf、list_local_reports、consensus_forecast/revisions",
    "research: local_stock_screen / dedup_articles（纯本地、不联网）",
    "announcements: download_announcement_pdf 实下 PDF（体量大，未默认探针）",
    "capital: block_trade、holder_num、dividend、corporate_actions、ownership_filings、northbound_daily 细节",
    "events/breadth: trade_calendar、suspensions、earnings_calendar、performance_updates、company_events、market_breadth、index_constituents 未加默认公网探针（有离线契约）",
    "valuation: forward_pe/peg/pe_digestion（纯计算）；一致预期见 research.consensus_forecast",
    "macro: 社融增量已接商务部；金十仅部分序列备胎（社融无）；财新 PMI/调查失业率/经济日历/惊喜/中债全曲线未探针",
    "macro: macro_snapshot 全腿（耗时长，仅分项探针 LPR/CPI/债汇）",
]


PROBES: List[Probe] = [
    Probe(
        "local_split_code",
        "代码/secid 本地契约",
        "core",
        "core.split_code / em_secid",
        True,
        _probe_split_code_local,
    ),
    Probe(
        "tencent_quote",
        "腾讯实时报价",
        "market",
        "market.tencent_quote",
        True,
        _probe_tencent_quote,
    ),
    Probe(
        "security_quote",
        "多源报价 fallback",
        "market",
        "market.security_quote",
        True,
        _probe_security_quote,
    ),
    Probe(
        "security_bars",
        "日 K fallback",
        "market",
        "market.security_bars",
        True,
        _probe_security_bars,
    ),
    Probe(
        "security_bars_index",
        "指数日 K",
        "market",
        "market.security_bars(index)",
        True,
        _probe_security_bars_index,
    ),
    Probe(
        "hk_us_quote",
        "港美多源报价",
        "market",
        "market.security_quote(hk/us)",
        True,
        _probe_hk_us_quote,
    ),
    Probe(
        "hk_us_bars", "港美日 K", "market", "market.security_bars(hk/us)", True, _probe_hk_us_bars
    ),
    Probe(
        "hk_us_stock_info",
        "港美个股资料",
        "fundamentals",
        "fundamentals.stock_info(hk/us)",
        False,
        _probe_hk_us_stock_info,
    ),
    Probe(
        "stock_info",
        "个股资料",
        "fundamentals",
        "fundamentals.stock_info",
        True,
        _probe_eastmoney_stock_info,
    ),
    Probe(
        "sina_financial",
        "三表利润表(A)",
        "fundamentals",
        "fundamentals.financial_report",
        False,
        _probe_sina_financial,
    ),
    Probe(
        "valuation_snapshot",
        "估值快照",
        "valuation",
        "valuation.valuation_snapshot",
        True,
        _probe_valuation_snapshot,
    ),
    Probe(
        "stock_announcements",
        "巨潮公告",
        "announcements",
        "announcements.stock_announcements",
        True,
        _probe_cninfo_announcements,
    ),
    Probe(
        "periodic_reports_a",
        "A股定期报告列表",
        "announcements",
        "announcements.periodic_reports(A)",
        False,
        _probe_periodic_reports_a,
    ),
    Probe(
        "periodic_reports_hk",
        "港股定期报告列表",
        "announcements",
        "announcements.periodic_reports(hk)",
        False,
        _probe_periodic_reports_hk,
    ),
    Probe(
        "periodic_reports_us",
        "美股SEC定期报告列表",
        "announcements",
        "announcements.periodic_reports(us)",
        False,
        _probe_periodic_reports_us,
    ),
    Probe("irm", "互动易", "announcements", "announcements.irm", False, _probe_cninfo_irm),
    Probe(
        "stock_news",
        "东财个股新闻",
        "feeds",
        "feeds.stock_news",
        False,
        _probe_eastmoney_stock_news,
    ),
    Probe("telegraph", "财联社电报", "feeds", "feeds.telegraph", False, _probe_cls_telegraph),
    Probe("global_news", "东财全球资讯", "feeds", "feeds.global_news", False, _probe_global_news),
    Probe(
        "southbound_daily",
        "南向日频",
        "capital",
        "capital.southbound_daily",
        True,
        _probe_southbound_daily,
    ),
    Probe(
        "fund_flow_120d",
        "120日资金流",
        "capital",
        "capital.stock_fund_flow_120d",
        False,
        _probe_fund_flow_120d,
    ),
    Probe(
        "concept_blocks",
        "个股概念板块",
        "capital",
        "capital.concept_blocks",
        False,
        _probe_concept_blocks,
    ),
    Probe(
        "margin_trading", "两融", "capital", "capital.margin_trading", False, _probe_margin_trading
    ),
    Probe(
        "industry_comparison",
        "行业涨跌榜",
        "capital",
        "capital.industry_comparison",
        False,
        _probe_industry_comparison,
    ),
    Probe(
        "daily_dragon_tiger",
        "全市场龙虎榜",
        "capital",
        "capital.daily_dragon_tiger",
        True,
        _probe_daily_dragon_tiger,
    ),
    Probe(
        "dragon_tiger_board",
        "个股龙虎席位",
        "capital",
        "capital.dragon_tiger_board",
        True,
        _probe_dragon_tiger_board,
    ),
    Probe(
        "lockup_expiry",
        "限售解禁",
        "announcements",
        "announcements.lockup_expiry",
        False,
        _probe_lockup_expiry,
    ),
    Probe(
        "local_report_search",
        "研报主题检索",
        "research",
        "research.local_report_search",
        False,
        _probe_local_report_search,
    ),
    Probe(
        "stock_reports",
        "个股研报列表",
        "research",
        "research.stock_reports",
        False,
        _probe_eastmoney_reports,
    ),
    Probe("macro_lpr", "LPR 利率", "macro", "macro.lpr_history", True, _probe_macro_lpr),
    Probe(
        "macro_cpi", "CPI 同比", "macro", "macro.cn_macro_series(cpi_yoy)", False, _probe_macro_cpi
    ),
    Probe(
        "macro_fx_or_bonds",
        "汇率或国债",
        "macro",
        "macro.fx_usdcny|bond_yields_cn_us",
        False,
        _probe_macro_fx_or_bonds,
    ),
]


def list_probes() -> List[Dict[str, Any]]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "group": p.group,
            "entry": p.entry,
            "critical": p.critical,
        }
        for p in PROBES
    ]


def run_self_check(
    groups: Optional[Iterable[str]] = None,
    only: Optional[Iterable[str]] = None,
    pace_sec: float = 0.6,
) -> SelfCheckReport:
    """运行自检并返回结构化报告。

    Args:
        groups: 仅跑这些 group（market/capital/…）
        only: 仅跑这些 probe id
        pace_sec: 探针间隔，降低东财断连

    自检使用与公开接口相同的执行协调与健康冷却，不绕过失败源状态。
    """
    group_set = {g.strip() for g in groups} if groups else None
    only_set = {x.strip() for x in only} if only else None
    unknown = (only_set or set()) - {probe.id for probe in PROBES}
    if unknown:
        raise ValueError("unknown probe IDs: " + ", ".join(sorted(unknown)))

    results: List[ProbeResult] = []
    selected = []
    for p in PROBES:
        if group_set and p.group not in group_set:
            continue
        if only_set and p.id not in only_set:
            continue
        selected.append(p)

    for i, p in enumerate(selected):
        t0 = time.time()
        try:
            status, structure_ok, message, sample, checks = p.run()
            err = None if status != "fail" else message
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            structure_ok = False
            message = "exception: %s" % exc
            sample = ""
            checks = []
            err = traceback.format_exc(limit=4)
        elapsed = (time.time() - t0) * 1000
        results.append(
            ProbeResult(
                id=p.id,
                name=p.name,
                group=p.group,
                entry=p.entry,
                status=status,
                critical=p.critical,
                elapsed_ms=round(elapsed, 1),
                message=message,
                structure_ok=structure_ok,
                sample=sample,
                error=err,
                checks=checks,
            )
        )
        if pace_sec > 0 and i < len(selected) - 1:
            time.sleep(pace_sec)

    counts = {"ok": 0, "degraded": 0, "fail": 0, "skip": 0}
    critical_fail = 0
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        if r.status == "fail" and r.critical:
            critical_fail += 1

    if critical_fail:
        summary = "关键通路有失败，取数能力受损，请先修 fail 项"
    elif counts["fail"]:
        summary = "非关键通路有失败；主链路大致可用"
    elif counts["degraded"]:
        summary = "全部可返回数据，但存在降级/字段残缺"
    else:
        summary = "全部探针通过"

    report = SelfCheckReport(
        generated_at=datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        code=CODE_SH,
        total=len(results),
        ok=counts["ok"],
        degraded=counts["degraded"],
        fail=counts["fail"],
        skip=counts["skip"],
        critical_fail=critical_fail,
        results=results,
        summary=summary,
    )
    return report


_STATUS_LABEL = {
    "ok": "✅ 通",
    "degraded": "⚠️ 降级",
    "fail": "❌ 断",
    "skip": "⏭ 跳过",
}


def render_markdown(report: SelfCheckReport) -> str:
    lines: List[str] = []
    lines.append("# Scutio 取数自检报告")
    lines.append("")
    lines.append("| 项 | 内容 |")
    lines.append("|----|------|")
    lines.append("| **时间** | %s |" % report.generated_at)
    lines.append("| **样本代码** | %s |" % report.code)
    lines.append(
        "| **汇总** | 通 %d · 降级 %d · 断 %d · 合计 %d |"
        % (report.ok, report.degraded, report.fail, report.total)
    )
    lines.append("| **关键失败** | %d |" % report.critical_fail)
    lines.append("| **结论** | %s |" % report.summary)
    lines.append("")
    lines.append("## 一览")
    lines.append("")
    lines.append("| 状态 | 关键 | 探针 | 模块 | 入口 | 说明 | 耗时 |")
    lines.append("|------|------|------|------|------|------|------|")
    order = {"fail": 0, "degraded": 1, "ok": 2, "skip": 3}
    for r in sorted(report.results, key=lambda x: (order.get(x.status, 9), x.group, x.id)):
        lines.append(
            "| %s | %s | %s | %s | `%s` | %s | %.0fms |"
            % (
                _STATUS_LABEL.get(r.status, r.status),
                "是" if r.critical else "",
                r.name,
                r.group,
                r.entry,
                (r.message or "").replace("|", "\\|")[:80],
                r.elapsed_ms,
            )
        )
    lines.append("")
    lines.append("## 明细（结构与样本）")
    lines.append("")
    for i, r in enumerate(
        sorted(report.results, key=lambda x: (order.get(x.status, 9), x.group, x.id)), 1
    ):
        lines.append("### %d. %s (`%s`)" % (i, r.name, r.id))
        lines.append("")
        lines.append("| 字段 | 内容 |")
        lines.append("|------|------|")
        lines.append("| **状态** | %s |" % _STATUS_LABEL.get(r.status, r.status))
        lines.append("| **关键** | %s |" % ("是" if r.critical else "否"))
        lines.append("| **入口** | `%s` |" % r.entry)
        lines.append("| **结构校验** | %s |" % ("通过" if r.structure_ok else "失败"))
        lines.append("| **说明** | %s |" % (r.message or "").replace("|", "\\|"))
        lines.append("| **检查点** | %s |" % (", ".join(r.checks) if r.checks else "—"))
        lines.append("| **样本摘要** | %s |" % (r.sample or "—").replace("|", "\\|")[:200])
        lines.append("| **耗时** | %.1f ms |" % r.elapsed_ms)
        lines.append("")
        if r.error and r.status == "fail":
            lines.append("```")
            lines.append((r.error or "")[:800])
            lines.append("```")
            lines.append("")
    lines.append("## 读法")
    lines.append("")
    lines.append("- **通**：联网成功且返回结构符合契约（关键字段/类型/信封）。")
    lines.append("- **降级**：主源挂了但备胎可用，或可选字段缺失；功能勉强可用。")
    lines.append("- **断**：异常、空结果不该空、或关键字段消失——优先怀疑上游改版/封禁。")
    lines.append("- 价格数值会变；自检**不**校验具体价，只校验「有价、有名、信封对」。")
    lines.append("- 龙虎榜：区分「接口失败」与「窗口内无上榜」（后者多为降级而非断）。")
    lines.append("")
    lines.append("## 仍未单独探针的能力（有意省略或已被组合覆盖）")
    lines.append("")
    for gap in COVERAGE_GAPS:
        lines.append("- %s" % gap)
    lines.append("")
    return "\n".join(lines)


def render_json(report: SelfCheckReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


# ── CLI ────────────────────────────────────────────────────────


def _ensure_scutio_data_on_path() -> None:
    """仓库根或任意 cwd 下都能 import scutio_data。"""
    import sys
    from pathlib import Path

    here = Path(__file__).resolve()
    # tests/self_check.py -> repo/skills/scutio/scripts
    candidates = [
        here.parents[1] / "skills" / "scutio" / "scripts",
    ]
    for scripts in candidates:
        if (scripts / "scutio_data").is_dir():
            s = str(scripts)
            if s not in sys.path:
                sys.path.insert(0, s)
            return
    # fallback: assume caller already set PYTHONPATH
    scripts_env = Path(__file__).resolve().parents[1] / "skills" / "scutio" / "scripts"
    s = str(scripts_env)
    if s not in sys.path:
        sys.path.insert(0, s)


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    _ensure_scutio_data_on_path()

    parser = argparse.ArgumentParser(description="Scutio 取数真实环境自检（tests/ 运维）")
    parser.add_argument(
        "--group",
        default="",
        help="逗号分隔 group：core,market,fundamentals,valuation,announcements,feeds,capital,research,macro",
    )
    parser.add_argument("--only", default="", help="逗号分隔 probe id")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="写入报告到该路径（默认 stdout；取数仍使用运行时缓存与状态）",
    )
    parser.add_argument("--pace", type=float, default=0.6, help="探针间隔秒（默认 0.6）")
    parser.add_argument("--list", action="store_true", help="列出探针后退出")
    args = parser.parse_args(argv)

    if args.list:
        for p in list_probes():
            flag = "*" if p["critical"] else " "
            print("%s %-22s %-12s %s" % (flag, p["id"], p["group"], p["name"]))
        print("\n* = critical")
        return 0

    groups = [x for x in args.group.split(",") if x.strip()] or None
    only = [x for x in args.only.split(",") if x.strip()] or None
    try:
        report = run_self_check(
            groups=groups,
            only=only,
            pace_sec=args.pace,
        )
    except ValueError as exc:
        parser.error(str(exc))
    text_out = render_json(report) if args.json else render_markdown(report)

    if args.output:
        out = Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text_out, encoding="utf-8")
        print("wrote %s" % out, file=__import__("sys").stderr)
        print(
            "summary: ok=%d degraded=%d fail=%d critical_fail=%d — %s"
            % (report.ok, report.degraded, report.fail, report.critical_fail, report.summary),
            file=__import__("sys").stderr,
        )
    else:
        print(text_out)

    if report.critical_fail:
        return 2
    if report.fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
