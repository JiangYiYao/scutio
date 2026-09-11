#!/usr/bin/env python3
"""报价侧估值快照；一致预期请用 research.eps_forecast + forward_pe/calc_peg。"""

from __future__ import annotations

import json
import sys

from _bootstrap import ensure_importable

ensure_importable()

from scutio_data.valuation import calc_peg, forward_pe, valuation_snapshot


def _show(code: str) -> dict:
    out = valuation_snapshot(code)
    print(f"## {code}")
    print(f"  ok={out.get('ok')} source={out.get('source')} error={out.get('error')}")
    if not out.get("ok"):
        print("  → 报价失败，勿用估值字段")
        return out
    print(
        f"  {out.get('name')} price={out.get('price')} "
        f"pe_ttm={out.get('pe_ttm')} pb={out.get('pb')} mcap_yi={out.get('mcap_yi')}"
    )
    print("  （一致预期/前向PE 不在本快照；见 research.eps_forecast）")
    return out


def main(argv: list[str] | None = None) -> int:
    codes = list(argv or [])[1:] or ["600519", "000858"]
    results = [_show(c) for c in codes]
    print("\n## json")
    print(
        json.dumps(
            [
                {
                    "code": r.get("code"),
                    "ok": r.get("ok"),
                    "source": r.get("source"),
                    "price": r.get("price"),
                    "pe_ttm": r.get("pe_ttm"),
                    "pb": r.get("pb"),
                }
                for r in results
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    # 演示公式（数值需自备 EPS）
    if results and results[0].get("ok") and results[0].get("price"):
        p = float(results[0]["price"])
        demo_eps = 50.0
        print(
            f"\n## formula demo price={p} eps={demo_eps} "
            f"forward_pe={forward_pe(p, demo_eps):.1f} "
            f"peg@20%={calc_peg(forward_pe(p, demo_eps), 0.2):.2f}"
        )
    if any(not r.get("ok") for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
