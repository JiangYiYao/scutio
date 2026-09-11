#!/usr/bin/env python3
"""Scutio 唯一采集 CLI：按问题选择模块，可复用适用的既有材料。"""

import argparse
import json
import sys
from pathlib import Path

# 固定使用当前 skill 自带的实现，不搜索历史安装目录。
_DIRECTORY = Path(__file__).resolve().parent
sys.path[:0] = [str(_DIRECTORY.parent), str(_DIRECTORY)]

from _collection_modules import MODULES  # noqa: E402
from _research_collection import collect  # noqa: E402, F401 - public callable entry
from scutio_data.paths import atomic_write_text  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", nargs="?", help="A/港/美完整代码；纯宏观请求可省略")
    parser.add_argument("--question", required=True)
    parser.add_argument("--modules", required=True, help=",".join(MODULES) + ",macro:<series>")
    parser.add_argument("--depth", choices=("light", "full"), default="light", help="仅控制条数")
    parser.add_argument(
        "--period", choices=("annual", "all", "quarter", "cumulative"), default="annual"
    )
    parser.add_argument(
        "--filing-kind", choices=("annual", "semi", "q1", "q3", "all"), default="annual"
    )
    parser.add_argument("--peers", type=Path, help="code/relation/basis 对象组成的 JSON 数组")
    parser.add_argument("--reuse", type=Path, help="适用的既有采集结果 JSON")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        reused = json.loads(args.reuse.read_text(encoding="utf-8")) if args.reuse else None
        peers = json.loads(args.peers.read_text(encoding="utf-8")) if args.peers else None
        data = collect(
            args.code,
            question=args.question,
            modules=args.modules,
            depth=args.depth,
            period=args.period,
            filing_kind=args.filing_kind,
            peers=peers,
            reuse=reused,
        )
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        if args.output:
            atomic_write_text(args.output, text + "\n")
        print(text)
        return 0 if data["ok"] else 2
    except (ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
