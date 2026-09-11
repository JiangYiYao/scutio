#!/usr/bin/env python3
"""通过龙虎榜门面查看上榜记录、覆盖范围和失败信息。"""

import json
import sys
from datetime import date, timedelta

from _bootstrap import ensure_importable

ensure_importable()
from scutio_data.capital import daily_dragon_tiger


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv)[1:]
    day = args[0] if args else (date.today() - timedelta(days=1)).isoformat()
    result = daily_dragon_tiger(day)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
