#!/usr/bin/env python3
"""港股 / 美股：报价、日 K、个股资料（显式代码）。"""

from __future__ import annotations

import json

from _bootstrap import ensure_importable

ensure_importable()

from scutio_data.fundamentals import stock_info
from scutio_data.market import security_bars, security_quote


def _show_quote(label: str, out: dict) -> None:
    print(f"## {label}")
    print(
        f"  ok={out.get('ok')} partial={out.get('partial')} "
        f"sources={out.get('sources_used')} error={out.get('error')}"
    )
    for sym, row in (out.get("quotes") or {}).items():
        if not isinstance(row, dict):
            continue
        # skip bare-code duplicate keys when full symbol present
        if sym in ("00700", "AAPL") and (out.get("quotes") or {}).get(
            "hk" + sym if sym.isdigit() else "us" + sym
        ):
            continue
        print(
            f"  {sym} {row.get('name')}: price={row.get('price')} "
            f"ccy={row.get('currency')} src={row.get('source')}"
        )


def main() -> int:
    # 美股必须显式 us* / *.US；裸 AAPL 会被拒绝
    codes = ["hk00700", "usAAPL"]
    q = security_quote(codes)
    _show_quote("security_quote HK+US（按市场自动选源）", q)

    complete = bool(q.get("ok")) and not q.get("partial", False)
    for code in codes:
        bars = security_bars(code, frequency="D", count=5)
        print(f"\n## security_bars {code}")
        print(
            f"  ok={bars.get('ok')} source={bars.get('source')} "
            f"adjust={bars.get('adjust')} n={len(bars.get('bars') or [])} "
            f"error={bars.get('error')}"
        )
        if bars.get("bars"):
            last = bars["bars"][-1]
            print(
                f"  last: {last.get('datetime')} O={last.get('open')} "
                f"H={last.get('high')} L={last.get('low')} C={last.get('close')}"
            )

        complete = complete and bool(bars.get("ok"))
        info = stock_info(code)
        complete = complete and bool(info.get("ok"))
        print(f"\n## stock_info {code}")
        print(
            json.dumps(
                {
                    "ok": info.get("ok"),
                    "error": info.get("error"),
                    "partial": info.get("partial"),
                    "name": info.get("name"),
                    "symbol": info.get("symbol"),
                    "industry": info.get("industry"),
                    "price": info.get("price"),
                    "source": info.get("source"),
                },
                ensure_ascii=False,
            )
        )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
