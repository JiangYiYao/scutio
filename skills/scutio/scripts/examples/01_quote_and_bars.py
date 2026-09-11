#!/usr/bin/env python3
"""报价与日 K：先看信封，再读字段。"""

from __future__ import annotations

import json

from _bootstrap import ensure_importable

ensure_importable()

from scutio_data.market import security_bars, security_quote


def main() -> int:
    codes = ["600519", "000001", "sh000001"]
    print("## security_quote (按配置和市场自动选源)")
    q = security_quote(codes)
    print(
        f"  ok={q.get('ok')} partial={q.get('partial')} "
        f"sources={q.get('sources_used')} error={q.get('error')}"
    )
    for code, row in (q.get("quotes") or {}).items():
        print(f"  {code}: price={row.get('price')} name={row.get('name')}")

    print("\n## security_bars 600519 D count=3")
    bars = security_bars("600519", frequency="D", count=3)
    print(
        f"  ok={bars.get('ok')} source={bars.get('source')} "
        f"n={len(bars.get('bars') or [])} errors={bars.get('errors')}"
    )
    for b in (bars.get("bars") or [])[:3]:
        print(f"  {b.get('date') or b.get('datetime')}: close={b.get('close')}")

    # 机器可读摘录
    print("\n## json snippet")
    print(
        json.dumps(
            {
                "security_quote_ok": q.get("ok"),
                "bars_ok": bars.get("ok"),
                "bars_source": bars.get("source"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if q.get("ok") and bars.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
