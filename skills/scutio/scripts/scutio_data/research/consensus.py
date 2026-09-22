"""公司年度盈利预测及机构预测修订。"""

import math
import re
from datetime import date

from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import (
    canonical_symbol,
    normalize_code,
    require_a_share,
    validate_report_identity,
)
from scutio_data._runtime.timeouts import operation
from scutio_data.research import discovery


@operation("history")
def eps_forecast(code):
    """AKShare 同花顺预测年报每股收益，保留各年度原生字段。"""
    try:
        _, prefix, pure = require_a_share(code, "eps_forecast")
        from scutio_data._providers.akshare.client import fetch

        rows = fetch("stock_profit_forecast_ths", symbol=pure, indicator="预测年报每股收益")
        if rows and any("年度" not in row for row in rows):
            raise ValueError("AKShare EPS year field missing")
        return result_list(
            rows,
            source="eps_forecast",
            adapter="akshare",
            symbol=prefix + pure,
            columns=list(rows[0]) if rows else [],
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="eps_forecast",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


def _forecast_number(value):
    try:
        if isinstance(value, bool) or value in (None, "", "-", "--"):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _map_ths_consensus_rows(rows):
    """兼容同花顺表格列名微调，统一为 year/eps/count。"""
    out = []
    for raw in rows or []:
        row = {str(k): v for k, v in dict(raw or {}).items()}
        year = None
        eps = None
        count = None
        high = None
        low = None
        for key, value in row.items():
            label = key.replace(" ", "")
            if year is None and any(token in label for token in ("年度", "年份", "报告期")):
                year = value
            if (
                eps is None
                and any(token in label for token in ("均值", "平均"))
                and "行业" not in label
            ):
                eps = value
            if count is None and "机构" in label and any(token in label for token in ("数", "家")):
                count = value
            if high is None and any(token in label for token in ("最大", "最高")):
                high = value
            if low is None and any(token in label for token in ("最小", "最低")):
                low = value
        if not re.fullmatch(r"\d{4}", str(year or "")):
            raise ValueError("AKShare consensus year is missing or invalid")
        if not any(
            any(token in key for token in ("均值", "平均")) and "行业" not in key for key in row
        ):
            raise ValueError("AKShare consensus mean EPS field missing")
        out.append(
            {
                "year": str(year),
                "eps": _forecast_number(eps),
                "analyst_count": _forecast_number(count),
                "report_count": None,
                "eps_high": _forecast_number(high),
                "eps_low": _forecast_number(low),
                "source": "ths_consensus",
            }
        )
    return out


@operation("history")
def consensus_forecast(code):
    """逐公司逐年度的同花顺一致预期。"""
    raw = eps_forecast(code)
    if not raw.get("ok"):
        return result_list_err(
            raw.get("error"), source="consensus_forecast", error_code=raw.get("error_code")
        )
    try:
        items = _map_ths_consensus_rows(raw.get("items") or [])
        return result_list(
            sorted(items, key=lambda item: item["year"]),
            source="ths_consensus",
            adapter="akshare",
            code=normalize_code(code),
            symbol=raw.get("symbol") or canonical_symbol(code),
            partial=True,
            backup_used=False,
            coverage={"per_company_years": True, "report_count": False},
            warning="Source does not expose report counts; empty rows mean no published forecast",
        )
    except Exception as exc:
        return result_list_err(str(exc), source="consensus_forecast")


def _conflicting_forecast_fields(rows):
    """Return explicit field disagreements within one available report-day group."""
    return [
        field
        for field in ("eps", "currency", "eps_basis", "eps_definition")
        if len({row[field] for row in rows if row[field] not in (None, "")}) > 1
    ]


def _forecast_observations(code, reports):
    """校验复用研报并生成唯一 EPS 观测口径；保留有争议的整组记录。"""
    upstream = {}
    try:
        _, prefix, pure = require_a_share(code, "forecast_observations")
        env = reports if isinstance(reports, dict) else result_list(list(reports))
        upstream = {key: env[key] for key in ("partial", "errors", "coverage") if key in env}
        if not env.get("ok"):
            return result_list_err(
                env.get("error") or "stock reports failed",
                source="forecast_observations",
                **upstream,
            )
        inputs = validate_report_identity(env, code)
        rows = []
        skipped = list(env.get("skipped_reports") or [])
        for index, report in enumerate(inputs):
            normalized = "fiscal_year" in report
            organization = (
                report.get("organization")
                if normalized
                else report.get("orgSName") or report.get("orgName")
            )
            years = [report.get("fiscal_year")] if normalized else report.get("forecast_years")
            info_code = report.get("info_code") if normalized else report.get("infoCode")
            context = {
                "index": index,
                "info_code": info_code or "",
                "organization": organization,
                "forecast_years": years,
                **{
                    key: report[key]
                    for key in ("published_at", "available_at")
                    if report.get(key) not in (None, "")
                },
            }
            if (
                not isinstance(years, list)
                or not years
                or len(years) > 3
                or any(
                    isinstance(year, bool) or not re.fullmatch(r"[12]\d{3}", str(year))
                    for year in years
                )
                or len(set(map(str, years))) != len(years)
            ):
                skipped.append({**context, "reason": "forecast_years_missing_or_invalid"})
                continue
            source_date = (
                report.get("source_date") or report.get("date")
                if normalized
                else report.get("publishDate") or report.get("publish_date")
            )
            try:
                published = date.fromisoformat(str(source_date or "")[:10]).isoformat()
            except ValueError:
                skipped.append({**context, "reason": "publication_date_missing_or_invalid"})
                continue
            if not isinstance(organization, str) or not organization.strip():
                skipped.append({**context, "reason": "organization_missing_or_invalid"})
                continue
            if any(
                report.get(field) is not None and not isinstance(report[field], str)
                for field in (
                    "currency",
                    "eps_basis",
                    "eps_definition",
                    "published_at",
                    "available_at",
                    "version",
                )
            ):
                skipped.append(
                    {**context, "date": published, "reason": "forecast_metadata_invalid"}
                )
                continue
            for offset, year in enumerate(years):
                field = (
                    "eps"
                    if normalized
                    else ("predictThisYearEps", "predictNextYearEps", "predictNextTwoYearEps")[
                        offset
                    ]
                )
                eps = _forecast_number(report.get(field))
                if eps is None:
                    skipped.append(
                        {
                            **context,
                            "forecast_years": [int(year)],
                            "date": published,
                            "reason": "eps_missing_or_invalid",
                        }
                    )
                    continue
                rows.append(
                    {
                        "code": pure,
                        "symbol": prefix + pure,
                        "date": published,
                        "source_date": source_date,
                        "published_at": report.get("published_at"),
                        "available_at": report.get("available_at"),
                        "organization": organization,
                        "fiscal_year": int(year),
                        "eps": eps,
                        "currency": report.get("currency"),
                        "eps_basis": report.get("eps_basis"),
                        "eps_definition": report.get("eps_definition"),
                        "version": report.get("version"),
                        "analyst": report.get("analyst")
                        or report.get("researcher")
                        or report.get("researcherName")
                        or "",
                        "rating": report.get("rating")
                        or report.get("emRatingName")
                        or report.get("ratingName")
                        or "",
                        "title": report.get("title") or "",
                        "info_code": info_code or "",
                        "report_url": report.get("report_url")
                        or report.get("pdf_url")
                        or report.get("pdfUrl"),
                        "retrieved_at": report.get("retrieved_at")
                        if normalized
                        else report.get("retrieved_at") or env.get("retrieved_at"),
                        "source": report.get("source")
                        if normalized
                        else report.get("source") or env.get("source"),
                        "report_references": [
                            dict(reference)
                            for reference in report.get("report_references") or []
                            if isinstance(reference, dict)
                        ],
                    }
                )
        # 相同机构、财年和日期的相同预测只计一次，保留全部报告位置。
        deduplicated = {}
        for row in rows:
            key = tuple(
                row.get(field)
                for field in (
                    "organization",
                    "fiscal_year",
                    "date",
                    "eps",
                    "currency",
                    "eps_basis",
                    "eps_definition",
                    "published_at",
                    "available_at",
                    "version",
                )
            )
            reference = {
                field: row[field]
                for field in (
                    "info_code",
                    "report_url",
                    "source",
                    "retrieved_at",
                    "title",
                    "analyst",
                )
            }
            if key in deduplicated:
                references = deduplicated[key]["report_references"]
                for candidate in row["report_references"] or [reference]:
                    if candidate not in references:
                        references.append(candidate)
            else:
                row["report_references"] = row["report_references"] or [reference]
                deduplicated[key] = row
        rows = list(deduplicated.values())
        daily = {}
        for row in rows:
            daily.setdefault((row["organization"], row["fiscal_year"], row["date"]), []).append(row)
        conflicts = []
        for (organization, year, published), group in daily.items():
            conflicting_fields = _conflicting_forecast_fields(group)
            reason = "same_day_forecast_conflict" if conflicting_fields else None
            for row in group:
                row.update(conflict=bool(reason), conflict_reason=reason)
            if reason:
                conflicts.append(
                    {
                        "organization": organization,
                        "fiscal_year": year,
                        "date": published,
                        "reason": reason,
                        "fields": conflicting_fields,
                        "eps_values": sorted({row["eps"] for row in group}),
                        "info_codes": sorted(
                            {
                                str(ref.get("info_code") or "")
                                for row in group
                                for ref in row["report_references"]
                            }
                        ),
                    }
                )
        upstream["partial"] = bool(
            upstream.get("partial")
            or skipped
            or conflicts
            or (env.get("coverage") or {}).get("truncated")
        )
        return result_list(
            sorted(
                rows,
                key=lambda item: (
                    item["date"],
                    item["organization"],
                    item["fiscal_year"],
                    str(item["info_code"]),
                ),
            ),
            source="forecast_observations",
            code=pure,
            symbol=prefix + pure,
            skipped_reports=skipped,
            conflicts=conflicts,
            input_source=env.get("input_source", env.get("source")),
            input_retrieved_at=env.get("input_retrieved_at", env.get("retrieved_at")),
            **upstream,
        )
    except (TypeError, ValueError, KeyError) as exc:
        return result_list_err(str(exc), source="forecast_observations", **upstream)


@operation("history")
def consensus_revisions(code, max_pages=5, reports=None):
    """按证券、机构、预测财年比较相邻 EPS；同日冲突整组不生成方向。"""
    try:
        require_a_share(code, "consensus_revisions")
        if reports is None:
            reports = discovery.stock_reports(code, max_pages=max_pages)
        env = _forecast_observations(code, reports)
        env["source"] = "consensus_revisions"
        if not env.get("ok"):
            return env
        prior_by_year = {}
        comparable = []
        for observation in env["items"]:
            row = dict(observation)
            group = (row["organization"], row["fiscal_year"])
            prior = prior_by_year.get(group)
            reason = (
                "ambiguous_current_day"
                if row["conflict"]
                else "ambiguous_previous_day"
                if prior and prior["conflict"]
                else "same_day_order_unknown"
                if prior and prior["date"] == row["date"]
                else "forecast_basis_mismatch"
                if prior
                and any(
                    row[field] not in (None, "")
                    and prior[field] not in (None, "")
                    and row[field] != prior[field]
                    for field in ("currency", "eps_basis", "eps_definition")
                )
                else None
            )
            change = row["eps"] - prior["eps"] if prior and reason is None else None
            row.update(
                comparison_reason=reason,
                previous_eps=prior["eps"] if prior and reason is None else None,
                previous_info_code=prior["info_code"] if prior else None,
                previous_date=prior["date"] if prior else None,
                change=round(change, 6) if change is not None else None,
                direction=("up" if change > 0 else "down" if change < 0 else "flat")
                if change is not None
                else "new"
                if prior is None and reason is None
                else None,
            )
            prior_by_year[group] = row
            comparable.append(row)
        comparable.sort(key=lambda item: (item["date"], item["fiscal_year"]), reverse=True)
        env.update(
            items=comparable,
            partial=bool(env["partial"] or any(row["comparison_reason"] for row in comparable)),
            revision_scope="broker_report_updates",
            note="按同一机构、同一预测财年比较 EPS；非供应商直接发布的汇总修订指标",
        )
        return env
    except (TypeError, ValueError, KeyError) as exc:
        return result_list_err(str(exc), source="consensus_revisions")
