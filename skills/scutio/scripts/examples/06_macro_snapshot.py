#!/usr/bin/env python3
"""宏观快照：利率 / 国债 / 汇率 / 指数 / 商品 + 一条 CPI 序列。"""

from __future__ import annotations

import json

from _bootstrap import ensure_importable

ensure_importable()

from scutio_data.macro import cn_macro_series, list_macro_series, macro_snapshot


def main() -> int:
    print("## supported series")
    for s in list_macro_series():
        print("  %s — %s" % (s["name"], s["description"]))

    print("\n## macro_snapshot")
    snap = macro_snapshot()
    print(
        "  ok=%s partial=%s source=%s error=%s"
        % (snap.get("ok"), snap.get("partial"), snap.get("source"), snap.get("error"))
    )
    if snap.get("errors"):
        print("  errors=%s" % snap.get("errors"))
    if snap.get("rates"):
        print("  rates.lpr=%s" % (snap["rates"].get("lpr") or {}))
        print("  rates.shibor=%s" % (snap["rates"].get("shibor") or {}))
    if snap.get("bonds"):
        b = snap["bonds"]
        print(
            "  bonds date=%s cn10y=%s us10y=%s" % (b.get("date"), b.get("cn_10y"), b.get("us_10y"))
        )
    if snap.get("fx"):
        print("  fx=%s" % snap["fx"])
    if snap.get("commodities"):
        for k, v in (snap["commodities"] or {}).items():
            print("  %s price=%s" % (k, (v or {}).get("price")))
    if snap.get("indices"):
        for row in (snap["indices"] or [])[:4]:
            print(
                "  idx %s %s px=%s chg=%s"
                % (row.get("code"), row.get("name"), row.get("price"), row.get("change_pct"))
            )

    print("\n## cn_macro_series('cpi_yoy', limit=3)")
    cpi = cn_macro_series("cpi_yoy", limit=3)
    items = cpi.get("items") or []
    print("  ok=%s n=%s err=%s" % (cpi.get("ok"), len(items), cpi.get("error")))
    for row in items[:3]:
        print("  %s value=%s unit=%s" % (row.get("date"), row.get("value"), row.get("unit")))

    print("\n## json keys")
    print(json.dumps(sorted(k for k in snap.keys() if k not in ("indices",)), ensure_ascii=False))
    return 0 if snap.get("ok") and cpi.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
