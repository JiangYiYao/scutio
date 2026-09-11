"""个股资金流与互联互通的日频口径。"""

from __future__ import annotations

from scutio_data._runtime.parsing import finite_number, source_date
from scutio_data._runtime.results import (
    envelope_items,
    result_err,
    result_list,
    result_list_err,
    result_ok,
)
from scutio_data._runtime.symbols import require_a_share, split_code
from scutio_data._runtime.timeouts import operation

# Eastmoney RPT_MUTUAL_DEAL_HISTORY MUTUAL_TYPE (verified 2026-07).
MUTUAL_TYPE = {
    "hgt": "001",
    "sgt": "003",
    "north": "005",
    "ggt_sh": "002",
    "ggt_sz": "004",
    "south": "006",
    "港股通": "006",
    "南向": "006",
    "北向": "005",
    "沪股通": "001",
    "深股通": "003",
}


def _mutual_type_code(kind):
    key = str(kind or "south").strip()
    if key in MUTUAL_TYPE:
        return MUTUAL_TYPE[key]
    if key in ("001", "002", "003", "004", "005", "006"):
        return key
    raise ValueError(
        "unknown mutual kind %r; use south/north/ggt_sh/ggt_sz/hgt/sgt or 001-006" % (kind,)
    )


@operation("history")
def mutual_connect_daily(kind="south", page_size=30):
    """AKShare 沪深港通成交历史；还原库的缩放以保持原字段数值口径。"""
    try:
        mtype = _mutual_type_code(kind)
        symbol = {
            "001": "沪股通",
            "003": "深股通",
            "005": "北向资金",
            "002": "港股通沪",
            "004": "港股通深",
            "006": "南向资金",
        }[mtype]
        index = {"001": "上证指数", "003": "深证指数", "002": "恒生指数", "004": "恒生指数"}.get(
            mtype, "沪深300"
        )
        from scutio_data._providers.akshare.client import fetch

        rows = fetch("stock_hsgt_hist_em", symbol=symbol)

        def number(row, key, factor=1):
            value = finite_number(row.get(key))
            return None if value is None else value * factor

        out = []
        for row in rows:
            buy, sell = number(row, "买入成交额", 100), number(row, "卖出成交额", 100)
            out.append(
                {
                    "date": source_date(row["日期"]),
                    "mutual_type": mtype,
                    "net_deal_amt": number(row, "当日成交净买额", 100),
                    "buy_amt": buy,
                    "sell_amt": sell,
                    "deal_amt": buy + sell if buy is not None and sell is not None else None,
                    "accum_deal_amt": number(
                        row, "历史累计净买额", 100 if mtype in ("001", "003") else 1000000
                    ),
                    "fund_inflow": number(row, "当日资金流入", 100),
                    "lead_code": row.get("领涨股-代码"),
                    "lead_name": row.get("领涨股"),
                    "lead_change_pct": number(row, "领涨股-涨跌幅"),
                    "index_close": number(row, index),
                    "index_change_pct": number(row, index + "-涨跌幅"),
                    "quota_text": None,
                    "deal_num": None,
                }
            )
        out.sort(key=lambda row: row["date"], reverse=True)
        selected = out[: max(1, int(page_size))]
        trade_fields = ("net_deal_amt", "buy_amt", "sell_amt", "deal_amt", "accum_deal_amt")
        trade_unit = "million_hkd" if mtype in ("002", "004", "006") else "million_cny"
        return result_list(
            selected,
            source="mutual_connect_daily:%s" % mtype,
            adapter="akshare",
            partial=True,
            coverage={
                "quota_text": False,
                "deal_num": False,
                "latest_trade_fields": {
                    name: bool(selected) and selected[0].get(name) is not None
                    for name in trade_fields
                },
            },
            amount_basis="eastmoney_raw_fields",
            units={
                **{name: trade_unit for name in trade_fields},
                "fund_inflow": "upstream_unspecified",
            },
            derived_fields=["deal_amt"],
            warning=(
                "Trade amounts are in millions of HKD (southbound) or CNY (northbound); "
                "missing northbound buy/sell/net values cannot establish net flows. "
                "Fund-inflow currency is not verified and must not be combined with trade amounts. "
                "Quota text and transaction counts are not exposed; aggregate index labels are not inferred"
            ),
        )
    except Exception as exc:
        return result_list_err(str(exc), source="mutual_connect_daily")


@operation("history")
def southbound_daily(page_size=30, detail=False):
    """港股通/南向日频。flat 返回 result_list；detail 返回 result_ok 嵌套。"""
    if not detail:
        return mutual_connect_daily("south", page_size=page_size)
    total = mutual_connect_daily("south", page_size=page_size)
    sh = mutual_connect_daily("ggt_sh", page_size=page_size)
    sz = mutual_connect_daily("ggt_sz", page_size=page_size)
    if not total.get("ok") and not sh.get("ok") and not sz.get("ok"):
        return result_err(
            total.get("error") or sh.get("error") or sz.get("error") or "all legs failed",
            source="southbound_daily",
            total=envelope_items(total),
            sh=envelope_items(sh),
            sz=envelope_items(sz),
        )
    return result_ok(
        source="southbound_daily",
        total=envelope_items(total),
        sh=envelope_items(sh),
        sz=envelope_items(sz),
        units=total.get("units") or sh.get("units") or sz.get("units") or {},
        coverage={
            key: env.get("coverage") for key, env in (("total", total), ("sh", sh), ("sz", sz))
        },
        warning=total.get("warning") or sh.get("warning") or sz.get("warning"),
        note="total=006 港股通合计; sh=002; sz=004",
        partial=any(not env.get("ok") or env.get("partial") for env in (total, sh, sz)),
    )


@operation("history")
def northbound_daily(page_size=30, detail=False):
    """北向日频。flat 返回 result_list；detail 返回 result_ok 嵌套。"""
    if not detail:
        return mutual_connect_daily("north", page_size=page_size)
    total = mutual_connect_daily("north", page_size=page_size)
    hgt = mutual_connect_daily("hgt", page_size=page_size)
    sgt = mutual_connect_daily("sgt", page_size=page_size)
    if not total.get("ok") and not hgt.get("ok") and not sgt.get("ok"):
        return result_err(
            total.get("error") or hgt.get("error") or sgt.get("error") or "all legs failed",
            source="northbound_daily",
            total=envelope_items(total),
            hgt=envelope_items(hgt),
            sgt=envelope_items(sgt),
        )
    return result_ok(
        source="northbound_daily",
        total=envelope_items(total),
        hgt=envelope_items(hgt),
        sgt=envelope_items(sgt),
        units=total.get("units") or hgt.get("units") or sgt.get("units") or {},
        coverage={
            key: env.get("coverage") for key, env in (("total", total), ("hgt", hgt), ("sgt", sgt))
        },
        warning=total.get("warning") or hgt.get("warning") or sgt.get("warning"),
        note="total=005; hgt=001; sgt=003",
        partial=any(not env.get("ok") or env.get("partial") for env in (total, hgt, sgt)),
    )


@operation("history")
def stock_fund_flow_120d(code):
    """Up to 120 chronological daily points through AKShare; no incomparable raw fallback."""
    try:
        require_a_share(code, "stock_fund_flow_120d")
        from scutio_data._providers.akshare.client import fetch

        market, pure = split_code(code)
        rows = fetch("stock_individual_fund_flow", stock=pure, market=market)
        fields = {
            "主力净流入-净额": "main_net",
            "超大单净流入-净额": "super_net",
            "大单净流入-净额": "large_net",
            "中单净流入-净额": "mid_net",
            "小单净流入-净额": "small_net",
        }
        items = [
            {
                "date": source_date(row["日期"]),
                **{target: finite_number(row.get(origin)) for origin, target in fields.items()},
            }
            for row in rows
        ]
        items = sorted(items, key=lambda row: row["date"])[-120:]
        if not items:
            raise ValueError("AKShare daily fund flow empty")
        return result_list(
            items,
            source="stock_fund_flow_120d",
            adapter="akshare",
            requested_count=120,
            available_count=len(items),
            partial=len(items) < 120,
            units={"*_net": "元"},
            identity_basis="requested_code",
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="stock_fund_flow_120d",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )
