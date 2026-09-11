#!/usr/bin/env python3
"""主题研报本地检索：东财列表 + 本地关键词/同义词评分（无需语义 key）。"""

from __future__ import annotations

import sys

from _bootstrap import ensure_importable

ensure_importable()

from scutio_data.research import dedup_articles, local_report_search


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])[1:]
    query = args[0] if args else "人形机器人 行星滚柱丝杠"
    begin = args[1] if len(args) > 1 else "2025-01-01"
    limit = int(args[2]) if len(args) > 2 else 15

    print(f"## local_report_search q={query!r} begin={begin} limit={limit}")
    env = local_report_search(query, begin=begin, limit=limit)
    if not env.get("ok"):
        print(f"  failed: {env.get('error')}")
        return 1
    reports = env.get("items") or []
    if not reports:
        print("  (合法空列表：关键词无命中)")
        return 0

    # 去重示例（按 uid/score）
    uniq = dedup_articles(reports)
    print(f"  raw={len(reports)} after_dedup={len(uniq)}")
    for i, r in enumerate(uniq[:10], 1):
        print(
            f"  {i}. {r.get('publishDate') or r.get('publish_date')} "
            f"score={r.get('_local_score')} "
            f"terms={r.get('_matched_terms')} "
            f"| {r.get('title')}"
        )
        info = r.get("infoCode") or r.get("info_code")
        if info:
            print(f"     infoCode={info}")

    print(
        "\n# 下载 PDF：dl=download_pdf(record); path/text_path → $SCUTIO_HOME/cache/documents/reports/{code}/；"
        "校验 %PDF 头；禁止并发打东财。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
