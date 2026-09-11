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
        if value in (None, "", "-", "--"):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
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


@operation("history")
def consensus_revisions(code, max_pages=5, reports=None):
    """按证券、机构、预测财年比较 EPS；复用材料必须携带证券身份。"""
    upstream = {}
    try:
        _, prefix, pure = require_a_share(code, "consensus_revisions")
        symbol = prefix + pure
        if reports is None:
            env = discovery.stock_reports(code, max_pages=max_pages)
        elif isinstance(reports, dict):
            env = reports
        else:
            env = result_list(list(reports), source="stock_reports_reused")
        upstream = {key: env[key] for key in ("partial", "errors", "coverage") if key in env}
        if not env.get("ok"):
            return result_list_err(
                env.get("error") or "stock reports failed", source="consensus_revisions", **upstream
            )
        inputs = validate_report_identity(env, code)
        rows, skipped = [], []
        for index, report in enumerate(inputs):
            organization = report.get("orgSName") or report.get("orgName")
            years = report.get("forecast_years")
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
                skipped.append({"index": index, "reason": "forecast_years_missing_or_invalid"})
                continue
            try:
                published = date.fromisoformat(
                    str(report.get("publishDate") or report.get("publish_date") or "")[:10]
                ).isoformat()
            except ValueError:
                skipped.append({"index": index, "reason": "publication_date_missing_or_invalid"})
                continue
            if not organization:
                skipped.append({"index": index, "reason": "organization_missing"})
                continue
            for offset, year in enumerate(years):
                field = ("predictThisYearEps", "predictNextYearEps", "predictNextTwoYearEps")[
                    offset
                ]
                eps = _forecast_number(report.get(field))
                if eps is None:
                    continue
                rows.append(
                    {
                        "date": published,
                        "organization": organization,
                        "fiscal_year": int(year),
                        "eps": eps,
                        "analyst": report.get("researcher") or report.get("researcherName") or "",
                        "rating": report.get("emRatingName") or report.get("ratingName") or "",
                        "title": report.get("title") or "",
                        "info_code": report.get("infoCode") or "",
                        "source": report.get("source") or env.get("source"),
                    }
                )
        daily_values = {}
        for row in rows:
            daily_values.setdefault(
                (row["organization"], row["fiscal_year"], row["date"]), set()
            ).add(row["eps"])
        ambiguous = {key for key, values in daily_values.items() if len(values) > 1}
        prior_by_year = {}
        seen = set()
        comparable = []
        for row in sorted(rows, key=lambda item: (item["date"], item["info_code"])):
            identity = (row["info_code"], row["fiscal_year"])
            if row["info_code"] and identity in seen:
                continue
            seen.add(identity)
            group = (row["organization"], row["fiscal_year"])
            prior = prior_by_year.get(group)
            # 日期相同而无更细发布时间时，不猜测同日研报的先后。
            reason = (
                "same_day_order_unknown"
                if prior and prior["date"] == row["date"]
                else "ambiguous_previous_day"
                if prior and (*group, prior["date"]) in ambiguous
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
                if prior is None
                else None,
            )
            prior_by_year[group] = row
            comparable.append(row)
        comparable.sort(key=lambda item: (item["date"], item["fiscal_year"]), reverse=True)
        upstream["partial"] = bool(
            upstream.get("partial")
            or skipped
            or ambiguous
            or (env.get("coverage") or {}).get("truncated")
        )
        return result_list(
            comparable,
            source="consensus_revisions",
            code=pure,
            symbol=symbol,
            revision_scope="broker_report_updates",
            skipped_reports=skipped,
            note="按同一机构、同一预测财年比较 EPS；非供应商直接发布的汇总修订指标",
            **upstream,
        )
    except (TypeError, ValueError, KeyError) as exc:
        return result_list_err(str(exc), source="consensus_revisions", **upstream)
