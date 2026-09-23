"""本地研报目录、相关检索、去重和本地条件筛选。"""

import re
from datetime import date, datetime

from scutio_data._documents.report_paths import (
    REPORT_CACHE_NOTE,
    _parse_date_from_filename,
    _report_code_from_record,
    default_reports_dir,
)
from scutio_data._providers import eastmoney_research
from scutio_data._runtime.environment import CN_TZ
from scutio_data._runtime.parsing import formatted_number as _number
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import (
    normalize_code,
    require_a_share,
    validate_report_identity,
)
from scutio_data.research import discovery


def _max_date(dates) -> str:
    vals = []
    for value in dates or []:
        try:
            parsed = date.fromisoformat(str(value or "")[:10]).isoformat()
        except ValueError:
            continue
        if parsed >= "1970-01-01":
            vals.append(parsed)
    return max(vals) if vals else ""


def _compare_report_dates(code, online, local_latest):
    """Only a complete, identified online list can establish that local files are current."""
    if not online.get("ok"):
        raise ValueError(online.get("error") or "stock_reports failed")
    rows = validate_report_identity(online, code)
    dates = [
        _max_date([row.get("publishDate") or row.get("publish_date") or row.get("date")])
        for row in rows
    ]
    online_latest = _max_date(dates)
    coverage = online.get("coverage") or {}
    if not isinstance(coverage, dict):
        raise ValueError("report coverage must be an object")
    reasons = []
    if online.get("partial"):
        reasons.append("stock_reports partial")
    if online.get("stale"):
        reasons.append("stock_reports stale")
    if online.get("error") or online.get("errors"):
        reasons.append(str(online.get("error") or online["errors"]))
    if coverage.get("truncated") or coverage.get("complete") is False:
        reasons.append("report coverage incomplete")
    for fetched, total in (("pages_fetched", "total_pages"), ("returned_count", "available_count")):
        have, expected = _number(coverage.get(fetched)), _number(coverage.get(total))
        if expected is not None and (have is None or have < expected):
            reasons.append("report coverage incomplete: %s < %s" % (fetched, total))
    if any(not value for value in dates):
        reasons.append("report publication date missing or invalid")
    partial = bool(reasons)
    return {
        "online_latest": online_latest or None,
        "online_n": len(rows),
        "online_error": "; ".join(reasons) or None,
        "partial": partial,
        "stale": True
        if online_latest and (not local_latest or local_latest < online_latest)
        else None
        if partial
        else False,
    }


def list_local_reports(code, compare_online=True, max_pages=2, online_reports=None):
    """列出本地已下载研报，并可与在线 ``stock_reports`` 比新鲜度。

    Args:
        code: A 股公司代码（决定 ``cache/documents/reports/{code}/``，纯本地模式也校验）。
        compare_online: True 时拉在线列表，填 ``online_latest`` / ``stale``。
        max_pages: 在线列表页数（仅 compare_online）。
        online_reports: 已取过的 ``stock_reports`` 信封或 rows；每条必须有匹配的
            证券身份，空列表须由信封明确证券。传入后不再联网。

    Returns:
        result_list：items 为本地文件；信封含
        ``local_latest`` / ``online_latest`` / ``stale`` / ``note``。
        **stale=True**：本地最新发布日 **早于** 在线列表最新日（或在线有、本地无）。
        在线结果失败、身份不符或不完整时保留本地文件并标记 ``partial`` /
        ``online_error``，不能确定是否落后时 ``stale=None``。
        禁止仅用本函数替代 ``stock_reports`` 做时效判断的唯一依据——
        仍应先看在线列表。
    """
    try:
        require_a_share(code, "list_local_reports")
        pure = normalize_code(code)
        code_seg = _report_code_from_record({"stockCode": pure})
        root = default_reports_dir(code_seg)
        items = []
        if root.is_dir():
            for pdf in sorted(root.glob("*.pdf")):
                pub = _parse_date_from_filename(pdf.name)
                txt = pdf.with_suffix(".txt")
                try:
                    mtime = datetime.fromtimestamp(pdf.stat().st_mtime, tz=CN_TZ).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    size = pdf.stat().st_size
                except OSError:
                    mtime = ""
                    size = 0
                items.append(
                    {
                        "path": str(pdf),
                        "text_path": str(txt) if txt.is_file() else None,
                        "name": pdf.name,
                        "publish_date": pub,
                        "mtime": mtime,
                        "bytes": size,
                        "code": code_seg,
                    }
                )

        local_latest = _max_date([x.get("publish_date") for x in items])
        comparison = {
            "online_latest": None,
            "online_n": None,
            "online_error": None,
            "stale": None,
            "partial": False,
        }
        if compare_online:
            try:
                if online_reports is None:
                    online = discovery.stock_reports(code, max_pages=max_pages)
                elif isinstance(online_reports, dict):
                    online = online_reports
                else:
                    online = result_list(list(online_reports), source="stock_reports_reused")
                if not isinstance(online, dict):
                    raise ValueError("stock_reports must return an envelope")
                comparison.update(
                    {
                        "online_" + key: online[key]
                        for key in ("coverage", "errors")
                        if key in online
                    }
                )
                comparison.update(_compare_report_dates(code, online, local_latest))
            except Exception as exc:
                comparison.update(partial=True, online_error=str(exc))

        stale = comparison["stale"]
        online_error = comparison["online_error"]
        note = REPORT_CACHE_NOTE
        if stale is True:
            note = "stale=True：在线列表有更新发布日（或本地尚无文件）；" + REPORT_CACHE_NOTE
        elif stale is False:
            note = (
                "stale=False：本地文件名日期不早于本次在线列表最新日；"
                "仍建议抽查 stock_reports 条目，勿只信本地。"
            )
        if compare_online and online_error:
            note = "在线对比不完整（%s）；%s" % (online_error, note)

        return result_list(
            items,
            source="list_local_reports",
            code=code_seg,
            dir=str(root),
            local_latest=local_latest or None,
            note=note,
            compare_online=bool(compare_online),
            **comparison,
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="list_local_reports",
            note=REPORT_CACHE_NOTE,
        )


def _query_terms(query, synonyms=None):
    """把主题查询展开为本地检索关键词列表。"""
    mapping = dict(_DEFAULT_THEME_SYNONYMS)
    mapping.update(synonyms or {})
    text = str(query).strip()
    raw = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", text)
    terms = set(raw)
    for phrase, expansions in mapping.items():
        if phrase.lower() in text.lower():
            terms.update(expansions)
    for token in raw:
        if len(token) <= 4:
            continue
        terms.update(token[index : index + 2] for index in range(len(token) - 1))
    return tuple(term for term in terms if len(term) > 1)


def local_report_search(
    query,
    records=None,
    begin=None,
    max_pages=5,
    limit=50,
    synonyms=None,
):
    """本地关键词检索研报（无需语义检索 key）。

    未传 records 时会按 begin/max_pages 拉行业研报再打分；
    命中记录带 ``_local_score`` / ``_matched_terms``。
    """
    if limit <= 0:
        return result_list([], source="local_report_search")
    terms = _query_terms(query, synonyms)
    if not terms:
        return result_list([], source="local_report_search")
    upstream = {}
    if records is not None:
        source = list(records)
    else:
        env = eastmoney_research.industry_reports("*", max_pages=max_pages, begin=begin)
        if isinstance(env, dict):
            upstream = {key: env[key] for key in ("partial", "errors", "coverage") if key in env}
            if env.get("ok") is False:
                return result_list_err(
                    env.get("error") or "industry_reports failed",
                    source="local_report_search",
                    **upstream,
                )
            source = list(env.get("items") or [])
        else:
            source = list(env or [])
    scored = []
    for record in source:
        text = " ".join(
            str(record.get(field, ""))
            for field in ("title", "reportTitle", "industryName", "stockName", "securityName")
        ).lower()
        matched = [term for term in terms if term.lower() in text]
        if not matched:
            continue
        result = dict(record)
        # 与匹配阶段同样做大小写不敏感计分（避免 AI 命中却 0 分）。
        result["_local_score"] = sum(5 for term in matched if term.lower() in text)
        result["_matched_terms"] = matched
        result["source"] = "eastmoney_local"
        scored.append(result)
    unique = dedup_articles(scored)
    unique.sort(
        key=lambda item: (
            item["_local_score"],
            str(item.get("publishDate", item.get("publish_date", ""))),
        ),
        reverse=True,
    )
    return result_list(
        unique[:limit],
        source="local_report_search",
        search_coverage={
            "candidates": len(source),
            "matched": len(unique),
            "returned": min(len(unique), limit),
            "truncated": len(unique) > limit,
        },
        **upstream,
    )


def dedup_articles(articles):
    """按文档标识或完整发布元数据去重，同名不同期保留。"""
    best = {}
    for article in articles:
        document_id = article.get("infoCode") or article.get("uid")
        key = (
            ("id", str(document_id))
            if document_id
            else (
                "metadata",
                article.get("title") or article.get("reportTitle") or "",
                article.get("publish_date") or article.get("publishDate") or "",
                article.get("orgSName") or article.get("orgName") or "",
                article.get("symbol") or article.get("stockCode") or "",
            )
        )
        score = article.get("score", article.get("_local_score", 0))
        best_score = best.get(key, {}).get("score", best.get(key, {}).get("_local_score", 0))
        if key not in best or float(score or 0) > float(best_score or 0):
            best[key] = article
    return sorted(
        best.values(),
        key=lambda item: item.get("publish_date", item.get("publishDate", "")),
        reverse=True,
    )


_DEFAULT_THEME_SYNONYMS = {
    "人形机器人": ("机器人", "减速器", "伺服", "丝杠", "传感器"),
    "机器人": ("自动化", "减速器", "伺服", "丝杠"),
    "ai算力": ("算力", "服务器", "光模块", "液冷", "数据中心"),
    "人工智能": ("AI", "算力", "大模型", "数据中心"),
    "低空经济": ("无人机", "eVTOL", "飞行汽车", "航空"),
}
