"""汇率与商品快照的领域结果。"""

from __future__ import annotations

from scutio_data._providers.sina_fx import _sina_hq_fetch
from scutio_data._runtime.parsing import finite_number
from scutio_data._runtime.results import result_err, result_ok
from scutio_data._runtime.timeouts import operation


@operation("query")
def fx_usdcny() -> dict:
    """在岸人民币兑美元（新浪 fx_susdcny）。"""
    try:
        q = _sina_hq_fetch("fx_susdcny")
        return result_ok(
            source="sina_fx_susdcny",
            pair="USDCNY",
            price=q.get("price"),
            bid=q.get("bid"),
            ask=q.get("ask"),
            name=q.get("name"),
            open=q.get("open"),
            prev_close=q.get("prev_close"),
            high=q.get("high"),
            low=q.get("low"),
            time=q.get("time"),
            date=q.get("date"),
            units={"price": "CNY per USD", "change_pct": "pct"},
            change_pct=q.get("change_pct"),
        )
    except Exception as exc:
        return result_err(exc, source="sina_fx_susdcny")


@operation("query")
def commodities_spot():
    """COMEX gold and NYMEX WTI futures quotes via AKShare; not physical spot prices."""
    from scutio_data._providers.akshare.client import fetch

    out, errors = {}, {}
    for key, code in (("gold", "GC"), ("wti", "CL")):
        try:
            rows = fetch("futures_foreign_commodity_realtime", symbol=[code])
            if len(rows) != 1 or finite_number(rows[0].get("最新价")) is None:
                raise ValueError("commodity quote missing")
            r = rows[0]
            out[key] = {
                "code": "hf_" + code,
                "name": r.get("名称"),
                "price": finite_number(r.get("最新价")),
                "open": finite_number(r.get("开盘价")),
                "high": finite_number(r.get("最高价")),
                "low": finite_number(r.get("最低价")),
                "last_settle_price": finite_number(r.get("昨日结算价")),
                "change_pct": finite_number(r.get("涨跌幅")),
                "time": r.get("行情时间"),
                "date": str(r.get("日期") or "")[:10],
                "asset_type": "futures",
                "currency": "USD",
                "source": "akshare_sina_futures",
                "identity_basis": "requested_symbol",
            }
        except Exception as exc:
            errors[key] = str(exc)
    if not out:
        return result_err("commodity quotes failed", source="akshare_sina_futures", errors=errors)
    return result_ok(
        source="akshare_sina_futures",
        gold=out.get("gold"),
        wti=out.get("wti"),
        partial=bool(errors),
        errors=errors,
    )
