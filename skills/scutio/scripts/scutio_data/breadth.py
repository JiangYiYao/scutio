"""市场宽度与指数成分。

A 股优先 AKShare 乐咕聚合，失败时使用 AKShare 新浪市场快照。
中证指数成分优先中证指数官网文件，新浪只作为覆盖较窄的备用源。"""

from __future__ import annotations

import math
from typing import Iterable, List

from scutio_data._runtime.results import (
    result_err,
    result_list,
    result_list_err,
    result_ok,
)
from scutio_data._runtime.timeouts import operation

__all__ = [
    "market_breadth",
    "index_constituents",
    "map_breadth_rows",
    "map_index_rows",
]


_MARKETS = frozenset(("a", "hk", "us"))


def _market_key(market):
    key = str(market or "a").strip().lower()
    key = {"cn": "a", "china": "a", "h": "hk", "usa": "us"}.get(key, key)
    if key not in _MARKETS:
        raise ValueError("market must be a/hk/us")
    return key


def _number(value):
    try:
        if value in (None, "", "-"):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def map_breadth_rows(rows: Iterable[dict], *, source: str, market: str) -> dict:
    changes: List[float] = []
    amount = 0.0
    amount_known = 0
    for row in rows or []:
        change = _number(
            row.get("f3") if "f3" in row else row.get("changepercent", row.get("涨跌幅"))
        )
        if change is None:
            continue
        changes.append(change)
        raw_amount = _number(row.get("f6") if "f6" in row else row.get("amount", row.get("成交额")))
        if raw_amount is not None:
            amount += raw_amount
            amount_known += 1
    total = len(changes)
    advancers = sum(1 for x in changes if x > 0)
    decliners = sum(1 for x in changes if x < 0)
    unchanged = total - advancers - decliners
    return {
        "market": market,
        "total": total,
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "net_advancers": advancers - decliners,
        "advance_decline_ratio": round(advancers / decliners, 4) if decliners else None,
        "advance_pct": round(advancers / total * 100, 4) if total else None,
        "decline_pct": round(decliners / total * 100, 4) if total else None,
        "amount": amount if amount_known else None,
        "amount_coverage": amount_known,
        "source": source,
    }


def _breadth_snapshot(function, market, source):
    from scutio_data._providers.akshare.client import fetch

    rows = fetch(function)
    if not rows:
        raise RuntimeError("market snapshot empty")
    out = map_breadth_rows(rows, source=source, market=market)
    # AKShare owns pagination. Exclude missing percentage changes, but disclose
    # the record count so missing/suspended quotes never become flat stocks.
    out.update(upstream_total=len(rows), complete=out["total"] == len(rows))
    return out


def _breadth_eastmoney(market: str) -> dict:
    functions = {"a": "stock_zh_a_spot_em", "us": "stock_us_spot_em"}
    return _breadth_snapshot(functions[market], market, "akshare_eastmoney")


def _breadth_sina_a() -> dict:
    return _breadth_snapshot("stock_zh_a_spot", "a", "akshare_sina")


def _breadth_legulegu_a() -> dict:
    """AKShare market activity aggregate; Scutio keeps breadth semantics."""
    from scutio_data._providers.akshare.client import fetch

    rows = fetch("stock_market_activity_legu")
    raw = {str(row.get("item") or "").strip(): row.get("value") for row in rows}
    values = {}
    for key, label in (
        ("advancers", "上涨"),
        ("decliners", "下跌"),
        ("unchanged", "平盘"),
        ("limit_up", "涨停"),
        ("limit_down", "跌停"),
        ("suspended", "停牌"),
        ("real_limit_up", "真实涨停"),
        ("real_limit_down", "真实跌停"),
    ):
        value = _number(raw.get(label))
        values[key] = int(value) if value is not None and value >= 0 else None
    required = (values.get("advancers"), values.get("decliners"), values.get("unchanged"))
    if any(value is None for value in required):
        raise RuntimeError("legulegu breadth schema changed")
    total = sum(required)
    advancers, decliners = values["advancers"], values["decliners"]
    return {
        "market": "a",
        "total": total,
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": values["unchanged"],
        "net_advancers": advancers - decliners,
        "advance_decline_ratio": round(advancers / decliners, 4) if decliners else None,
        "advance_pct": round(advancers / total * 100, 4) if total else None,
        "decline_pct": round(decliners / total * 100, 4) if total else None,
        "limit_up": values["limit_up"],
        "limit_down": values["limit_down"],
        "suspended": values["suspended"],
        "real_limit_up": values["real_limit_up"],
        "real_limit_down": values["real_limit_down"],
        "as_of": str(raw.get("统计日期") or ""),
        "amount": None,
        "amount_coverage": 0,
        "source": "akshare_legulegu",
        "upstream_total": total,
        "complete": False,
        "coverage": {
            "universe": "sse_szse_a_shares",
            "missing_exchanges": ["bj"],
            "total_basis": "advancers+decliners+unchanged",
            "suspended_in_total": False,
        },
    }


@operation("batch")
def market_breadth(market="a", fallback=True):
    """市场涨跌家数宽度。A 股乐咕→新浪；美股东财；港股暂不可用。"""
    try:
        key = _market_key(market)
        if key == "hk":
            return result_err(
                "HK market breadth unavailable: snapshot source disabled",
                source="market_breadth",
                market=key,
                unavailable=True,
                reason="source_disabled",
            )
        if key == "a":
            try:
                data = _breadth_legulegu_a()
                return result_ok(
                    source=data.pop("source"),
                    backup_used=False,
                    partial=True,
                    secondary_fields=["limit_up", "limit_down", "real_limit_up", "real_limit_down"],
                    note="涨跌家数为本门面主口径；涨跌停聚合数仅作市场背景辅助，不生成个股打板信号",
                    **data,
                )
            except Exception as primary_exc:
                if not fallback:
                    raise
                try:
                    data = _breadth_sina_a()
                    return result_ok(
                        source=data.pop("source"),
                        backup_used=True,
                        primary_error=str(primary_exc),
                        partial=not data.get("complete", True),
                        secondary_fields=[
                            "limit_up",
                            "limit_down",
                            "real_limit_up",
                            "real_limit_down",
                        ],
                        note="涨跌家数为本门面主口径；涨跌停聚合数仅作市场背景辅助，不生成个股打板信号",
                        **data,
                    )
                except Exception as backup_exc:
                    raise RuntimeError(
                        "legulegu: %s; sina: %s" % (primary_exc, backup_exc)
                    ) from backup_exc
        try:
            data = _breadth_eastmoney(key)
            return result_ok(
                source=data.pop("source"),
                backup_used=False,
                partial=not data.get("complete", True),
                **data,
            )
        except Exception:
            raise
    except Exception as exc:
        return result_err(str(exc), source="market_breadth", market=str(market))


def map_index_rows(records: Iterable[dict], *, source: str, index_code: str) -> List[dict]:
    out = []
    for row in records or []:
        code = (
            row.get("成分券代码") or row.get("symbol") or row.get("code") or row.get("代码") or ""
        )
        code = (
            str(code)
            .split(".")[0]
            .strip()
            .removeprefix("sh")
            .removeprefix("sz")
            .removeprefix("bj")
            .zfill(6)
        )
        weight = row.get("权重") if "权重" in row else row.get("weight")
        out.append(
            {
                "index_code": str(row.get("指数代码") or index_code).zfill(6),
                "index_name": row.get("指数名称") or row.get("index_name") or "",
                "date": str(row.get("日期") or row.get("date") or "")[:10],
                "code": code,
                "name": row.get("成分券名称") or row.get("name") or row.get("名称") or "",
                "exchange": row.get("交易所") or row.get("exchange") or "",
                "weight_pct": _number(weight),
                "source": source,
            }
        )
    return out


def _index_csindex(index_code: str, include_weights: bool) -> tuple[list, bool, str | None]:
    from scutio_data._providers.akshare.client import fetch

    items = map_index_rows(
        fetch("index_stock_cons_csindex", symbol=index_code),
        source="akshare_csindex",
        index_code=index_code,
    )
    if not include_weights:
        return items, False, None
    try:
        weights = map_index_rows(
            fetch("index_stock_cons_weight_csindex", symbol=index_code),
            source="akshare_csindex",
            index_code=index_code,
        )
        by_code = {row["code"]: row for row in weights}
        for item in items:
            weight = by_code.get(item["code"], {})
            item["weight_pct"] = weight.get("weight_pct")
            item["weight_date"] = weight.get("date")
        missing = any(item["weight_pct"] is None for item in items)
        return items, missing, "Some constituents have no published weight" if missing else None
    except Exception as exc:
        return items, True, str(exc)


def _index_sina(index_code: str) -> List[dict]:
    from scutio_data._providers.akshare.client import fetch

    rows = fetch("index_stock_cons_sina", symbol=index_code)
    if not rows:
        raise RuntimeError("sina index constituents empty")
    return map_index_rows(rows, source="akshare_sina", index_code=index_code)


@operation("history")
def index_constituents(index_code="000300", include_weights=True, fallback=True):
    """中证指数最新成分与权重；官网文件主源、覆盖较窄的新浪 backup。"""
    try:
        code = "".join(ch for ch in str(index_code) if ch.isdigit()).zfill(6)
        if len(code) != 6:
            raise ValueError("invalid index_code %r" % index_code)
        try:
            items, partial, weight_error = _index_csindex(code, bool(include_weights))
            if not items:
                raise RuntimeError("csindex constituents empty")
            return result_list(
                items,
                source="akshare_csindex",
                index_code=code,
                include_weights=bool(include_weights),
                partial=partial,
                weight_error=weight_error,
                backup_used=False,
            )
        except Exception as primary_exc:
            if not fallback:
                raise
            try:
                items = _index_sina(code)
                return result_list(
                    items,
                    source="akshare_sina",
                    index_code=code,
                    include_weights=False,
                    partial=bool(include_weights),
                    weight_error=("新浪 backup 不提供权重" if include_weights else None),
                    backup_used=True,
                    primary_error=str(primary_exc),
                )
            except Exception as backup_exc:
                raise RuntimeError(
                    "csindex: %s; sina: %s" % (primary_exc, backup_exc)
                ) from backup_exc
    except Exception as exc:
        return result_list_err(str(exc), source="index_constituents")
