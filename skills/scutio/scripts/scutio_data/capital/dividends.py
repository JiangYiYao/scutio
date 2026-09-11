"""分红事件归并、普通及特别分红覆盖和可选服务回退。"""

from __future__ import annotations

import re
from decimal import Decimal

from scutio_data._runtime.parsing import finite_number, source_date
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import require_a_share
from scutio_data._runtime.timeouts import operation


def _dividend_events(code):
    """Implemented CNINFO components, including annual and special dividends."""
    from scutio_data._providers.akshare.client import fetch

    _, _, pure = require_a_share(code, "dividend_history")
    aliases = {
        "除权日": "EX_DIVIDEND_DATE",
        "实施方案分红说明": "IMPL_PLAN_PROFILE",
        "实施方案公告日期": "NOTICE_DATE",
        "股权登记日": "EQUITY_RECORD_DATE",
        "派息日": "PAY_CASH_DATE",
        "分红类型": "DIVIDEND_TYPE",
        "报告时间": "REPORT_PERIOD",
    }
    return [
        {
            **{aliases.get(key, key): value for key, value in row.items()},
            "ASSIGN_PROGRESS": "实施方案",
        }
        for row in fetch("stock_dividend_cninfo", symbol=pure)
    ]


def _dividend_event_rows(events):
    """Aggregate distinct implemented components; never add old summary rows."""
    by_date = {}
    errors = []
    event_dates = [source_date(row.get("EX_DIVIDEND_DATE")) for row in events]
    seen = set()
    for event, day in zip(events, event_dates):
        if not day or event.get("ASSIGN_PROGRESS") != "实施方案":
            continue
        identity = tuple(
            str(event.get(key) or "")
            for key in (
                "EX_DIVIDEND_DATE",
                "IMPL_PLAN_PROFILE",
                "DIVIDEND_TYPE",
                "REPORT_PERIOD",
                "NOTICE_DATE",
            )
        )
        if identity in seen:
            continue
        seen.add(identity)
        profile = str(event.get("IMPL_PLAN_PROFILE") or "").replace(" ", "")
        base = re.match(r"(?:每)?(\d+(?:\.\d+)?)(?:股)?", profile)
        parts = re.findall(r"(转增|转|送|派)(\d+(?:\.\d+)?)", profile)
        if not base or Decimal(base[1]) <= 0 or not parts:
            errors.append("Unparsed dividend plan on %s: %s" % (day, profile))
            by_date.setdefault(day, []).append(None)
            continue
        values = {"cash": Decimal(0), "bonus": Decimal(0), "transfer": Decimal(0)}
        for label, value in parts:
            key = "cash" if label == "派" else "bonus" if label == "送" else "transfer"
            values[key] += Decimal(value) * 10 / Decimal(base[1])
        by_date.setdefault(day, []).append(
            {
                "plan_text": profile,
                "notice_date": source_date(event.get("NOTICE_DATE")),
                "record_date": source_date(event.get("EQUITY_RECORD_DATE")),
                "payment_date": source_date(event.get("PAY_CASH_DATE")),
                **values,
            }
        )
    out = {}
    for day, components in by_date.items():
        if any(component is None for component in components):
            continue
        cash = sum((component["cash"] for component in components), Decimal(0))
        out[day] = {
            "date": day,
            "bonus_rmb": float(cash),
            "dividend_per_share": float(cash / 10),
            "bonus_ratio": float(sum(component["bonus"] for component in components)),
            "transfer_ratio": float(sum(component["transfer"] for component in components)),
            "plan": "实施分配",
            "source": "akshare_cninfo",
            "components": [
                {
                    key: float(value) if isinstance(value, Decimal) else value
                    for key, value in component.items()
                }
                for component in components
            ],
        }
    return out, None, errors


@operation("history")
def dividend_history(code, page_size=20, *, sources=None):
    from scutio_data._providers.hithink import client as hithink

    errors = {}
    try:
        require_a_share(code, "dividend_history")
        if int(page_size) < 1:
            raise ValueError("page_size must be positive")
        chain = (
            tuple(sources)
            if sources is not None
            else (("hithink", "akshare") if hithink.preferred(code) else ("akshare",))
        )
        for source in chain:
            try:
                if source == "hithink":
                    result = hithink.dividends(code, int(page_size))
                elif source in ("akshare", "eastmoney", "public"):
                    result = _dividend_history_public(code, page_size)
                else:
                    raise ValueError("unsupported dividend source")
                if result.get("ok"):
                    result.update(
                        attempted_sources=[*errors, source],
                        sources_used=[source],
                        fallback_reason=errors or None,
                    )
                    return result
                errors[source] = result.get("error")
            except Exception as exc:
                errors[source] = str(exc)
        return result_list_err(
            "; ".join(str(v) for v in errors.values()), source="dividend_history", errors=errors
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="dividend_history",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


def _dividend_history_public(code, page_size=20):
    try:
        _, _, pure = require_a_share(code, "dividend_history")
        limit = int(page_size)
        if limit < 1:
            raise ValueError("page_size must be positive")
        errors = []
        # A single complete component source avoids adding regular dividends twice.
        try:
            corrections, _, parse_errors = _dividend_event_rows(_dividend_events(code))
            if not parse_errors:
                items = sorted(corrections.values(), key=lambda row: row["date"], reverse=True)[
                    :limit
                ]
                return result_list(
                    items,
                    source="akshare_cninfo",
                    partial=False,
                    warning=None,
                    special_dividend_coverage={"available": True, "after": None},
                    units={
                        "bonus_rmb": "CNY per 10 shares",
                        "dividend_per_share": "CNY per share",
                        "bonus_ratio": "shares per 10 shares",
                        "transfer_ratio": "shares per 10 shares",
                    },
                )
            errors.extend(parse_errors)
        except Exception as exc:
            corrections = {}
            errors.append("Special dividend coverage unavailable: %s" % exc)
        from scutio_data._providers.akshare.client import fetch

        fallback_source = "akshare_eastmoney"
        try:
            records = fetch("stock_fhps_detail_em", symbol=pure)
        except Exception as exc:
            errors.append("Regular dividend coverage unavailable: %s" % exc)
            if not corrections:
                raise RuntimeError("; ".join(errors)) from exc
            records = []
            fallback_source = "akshare_cninfo"
        aliases = {
            "除权除息日": "EX_DIVIDEND_DATE",
            "现金分红-现金分红比例": "PRETAX_BONUS_RMB",
            "送转股份-转股比例": "TRANSFER_RATIO",
            "送转股份-送股比例": "BONUS_RATIO",
            "方案进度": "ASSIGN_PROGRESS",
        }
        rows = [{aliases.get(key, key): value for key, value in row.items()} for row in records]
        items = [
            {
                "date": source_date(x.get("EX_DIVIDEND_DATE")),
                "bonus_rmb": x.get("PRETAX_BONUS_RMB"),
                "dividend_per_share": (
                    finite_number(x.get("PRETAX_BONUS_RMB")) / 10
                    if finite_number(x.get("PRETAX_BONUS_RMB")) is not None
                    else None
                ),
                "transfer_ratio": x.get("TRANSFER_RATIO"),
                "bonus_ratio": x.get("BONUS_RATIO"),
                "plan": x.get("ASSIGN_PROGRESS", ""),
                "source": "akshare_eastmoney",
            }
            for x in rows
        ]
        items = [item for item in items if item["date"] not in corrections]
        items.extend(corrections.values())
        items.sort(key=lambda item: item.get("date") or "", reverse=True)
        items = items[:limit]
        return result_list(
            items,
            source=fallback_source,
            partial=True,
            warning="; ".join(errors) or None,
            special_dividend_coverage={"available": False, "after": None},
            units={
                "bonus_rmb": "CNY per 10 shares",
                "dividend_per_share": "CNY per share",
                "bonus_ratio": "shares per 10 shares",
                "transfer_ratio": "shares per 10 shares",
            },
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="dividend_history",
            error_code=(
                str(exc).split(":", 1)[0]
                if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
                else None
            ),
        )
