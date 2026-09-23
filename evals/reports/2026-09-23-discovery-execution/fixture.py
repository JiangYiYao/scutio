"""Frozen public-facade responses for behavior checks, not live integration tests."""

import inspect
import json
from pathlib import Path


def enable(trace):
    from scutio_data import events, feeds, fundamentals, market, screening, universe

    trace = Path(trace)

    def log(name, args):
        with trace.open("a", encoding="utf-8") as out:
            out.write(
                json.dumps({"call": name, "args": args}, ensure_ascii=False, default=str) + "\n"
            )

    names = ["澄原设备", "柏泉消费", "远澜技术", "恒渠服务", "渚云制造", "流岚软件"]
    symbols = ["sh600101", "sz000101", "sh688101", "sz000102", "bj920101", "sh688102"]
    prices = [20, 30, 25, 12, 18, 60]
    caps = [40, 30, 50, 24, 18, 120]
    pes = [16, 15, None, 12, None, 55]
    profits = [1.44, 0.9, -0.4, 1.02, None, 1.2]
    growth = [20, 80, None, 2, None, 50]
    revenues = [12, 6, 2, 8.2, 5, 9]
    rev_growth = [20, 20, 25, 2.5, 25, 50]
    rows = [
        {
            "symbol": s,
            "code": s[2:],
            "name": names[i],
            "exchange": s[:2],
            "price": prices[i],
            "mcap_yi": caps[i],
            "pe_ttm": pes[i],
            "pb": [2, 2.1, 4, 1.4, 1.8, 6][i],
            "net_profit": None if profits[i] is None else profits[i] * 1e8,
            "revenue": revenues[i] * 1e8,
            "net_profit_growth_pct": growth[i],
            "net_profit_yoy": growth[i],
            "revenue_growth_pct": rev_growth[i],
            "revenue_yoy": rev_growth[i],
            "source": "fixture",
            "report_date": "2026-06-30",
            "currency": "CNY",
        }
        for i, s in enumerate(symbols)
    ]
    original_catalog = screening.feature_catalog
    signature = inspect.signature(screening.screen_market)

    def catalog():
        log("feature_catalog", {})
        return original_catalog()

    def screen(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        p = bound.arguments
        log("screen_market", p)
        if p["market"] != "a" or p["report_date"] not in (None, "2026-06-30"):
            return {
                "ok": False,
                "error": "fixture only supplies A shares and 2026-06-30",
                "items": [],
            }
        selected = (
            rows
            if p["codes"] is None
            else [r for r in rows if r["symbol"] in p["codes"] or r["code"] in p["codes"]]
        )
        result = screening.screen_records(
            selected,
            p["filters"],
            universe="six fictional A-share records only",
            as_of="2026-09-23",
            sort_by=p["sort_by"],
            descending=p["descending"],
            limit=p["limit"],
        )
        result.update(
            source="fixture",
            partial=True,
            coverage={"universe_complete": None, "screen_complete": False},
            point_in_time_verified=False,
        )
        return result

    def directory(market="a"):
        log("stock_universe", {"market": market})
        return {"ok": True, "items": rows, "source": "fixture", "is_complete": None}

    def quote(codes, **kwargs):
        log("security_quote", {"codes": codes, **kwargs})
        return {
            "ok": True,
            "quotes": {r["symbol"]: r for r in rows if r["symbol"] in codes or r["code"] in codes},
            "source": "fixture",
        }

    def financial(report_date, *, codes=None):
        log("financial_snapshot", {"report_date": report_date, "codes": codes})
        return {
            "ok": True,
            "items": [
                r for r in rows if codes is None or r["symbol"] in codes or r["code"] in codes
            ],
            "source": "fixture",
            "report_date": report_date,
            "partial": True,
        }

    def signals(*args, **kwargs):
        log("disclosure_index", {"args": args, **kwargs})
        return {
            "ok": True,
            "source": "fixture",
            "partial": True,
            "items": [
                {"symbol": "sh600101", "title": "客户设备更新及订单", "path": "companies.md"},
                {
                    "symbol": "sh688101",
                    "title": "新工艺客户测试通过并签有条件采购协议",
                    "path": "companies.md",
                },
                {"symbol": "sz000101", "title": "利润中包含重大处置收益", "path": "companies.md"},
            ],
        }

    screening.feature_catalog = catalog
    screening.screen_market = screen
    universe.stock_universe = directory
    market.security_quote = quote
    fundamentals.financial_snapshot = financial
    events.company_events = signals
    events.performance_updates = signals
    feeds.telegraph = signals

    import requests

    def blocked(*args, **kwargs):
        raise RuntimeError("offline fixture: no additional data available")

    requests.sessions.Session.request = blocked
