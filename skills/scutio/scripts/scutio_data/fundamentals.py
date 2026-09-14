"""个股档案、三表与资料长文。"""

from __future__ import annotations

from scutio_data._runtime.results import (
    result_err,
    result_list,
    result_list_err,
    result_ok,
)
from scutio_data._runtime.symbols import (
    require_a_share,
    require_company,
    validate_identity,
)
from scutio_data._runtime.timeouts import operation

__all__ = [
    "stock_info",
    "financial_report",
    "stock_materials",
]


# 统一 report_type → 内部码；A=新浪 source；港/美再映射中文表名。
_REPORT_TYPE_ALIASES = {
    "lrb": "lrb",
    "profit": "lrb",
    "income": "lrb",
    "利润表": "lrb",
    "综合损益表": "lrb",
    "损益表": "lrb",
    "llb": "llb",
    "cash": "llb",
    "cashflow": "llb",
    "现金流量表": "llb",
    "fzb": "fzb",
    "zcfzb": "fzb",
    "zcfz": "fzb",
    "balance": "fzb",
    "bs": "fzb",
    "资产负债表": "fzb",
}


# 港股东财表名
_HK_REPORT_NAME = {
    "lrb": "利润表",
    "llb": "现金流量表",
    "fzb": "资产负债表",
}


# 美股东财表名（利润表叫综合损益表）
_US_REPORT_NAME = {
    "lrb": "综合损益表",
    "llb": "现金流量表",
    "fzb": "资产负债表",
}


# period：annual=年报/年度；all=港股报告期；quarter/cumulative=美股
_PERIOD_ALIASES = {
    "annual": "annual",
    "year": "annual",
    "y": "annual",
    "年报": "annual",
    "年度": "annual",
    "all": "all",
    "report": "all",
    "报告期": "all",
    "interim": "all",
    "quarter": "quarter",
    "q": "quarter",
    "单季报": "quarter",
    "cumulative": "cumulative",
    "ytd": "cumulative",
    "累计季报": "cumulative",
}


def _stock_info(code):
    """AKShare 个股档案；缺失市场返回明确的报价子集。"""
    mkt, prefix, pure = require_company(code, "stock_info")
    # Keep full code for secid; pure for display fields.
    full = prefix + pure
    order = ("eastmoney", "tencent")

    def _from_em():
        if mkt == "a":
            from scutio_data._providers.akshare.client import fetch

            rows = fetch("stock_individual_info_em", symbol=pure, timeout=8)
            values = {row["item"]: row["value"] for row in rows}
            if str(values.get("股票代码")) != pure:
                raise ValueError("stock info identity mismatch")
            return {
                "code": pure,
                "name": values.get("股票简称"),
                "industry": values.get("行业"),
                "total_shares": values.get("总股本"),
                "float_shares": values.get("流通股"),
                "mcap": values.get("总市值"),
                "float_mcap": values.get("流通市值"),
                "list_date": str(values.get("上市时间") or ""),
                "price": values.get("最新"),
                "exchange": prefix,
                "symbol": full,
                "source": "akshare_eastmoney",
            }
        if mkt == "hk":
            from scutio_data._providers.akshare.client import fetch

            rows = fetch("stock_hk_company_profile_em", symbol=pure)
            if len(rows) != 1:
                raise ValueError("HK company profile missing")
            row = rows[0]
            return {
                "code": pure,
                "name": row.get("公司名称"),
                "industry": row.get("所属行业"),
                "total_shares": None,
                "float_shares": None,
                "mcap": None,
                "float_mcap": None,
                "list_date": None,
                "price": None,
                "exchange": prefix,
                "symbol": full,
                "source": "akshare_hk_profile",
                "data_quality": "partial_fallback",
                "profile": row,
                "identity_basis": "requested_symbol",
            }
        raise ValueError("AKShare profile unavailable for this asset; quote subset only")

    def _from_tencent():
        from scutio_data._providers.quotes import tencent_quote

        batch = tencent_quote([full])
        row = batch.get(full) or batch.get(pure) or {}
        # A full provider dictionary key is also identity evidence; explicit row
        # claims must still agree with it, including exchange/code conflicts.
        validate_identity({**({"symbol": full} if full in batch else {}), **row}, full)
        if not row or not (row.get("name") or row.get("price")):
            raise ValueError("empty tencent stock info fallback")
        mcap_yi = row.get("mcap_yi")
        float_mcap_yi = row.get("float_mcap_yi")
        return {
            "code": pure,
            "name": row.get("name") or "",
            "industry": "",
            "total_shares": None,
            "float_shares": None,
            # mcap fields: tencent is 亿；keep raw 亿*1e8 if numeric for rough parity
            "mcap": float(mcap_yi) * 1e8 if mcap_yi is not None else None,
            "float_mcap": (float(float_mcap_yi) * 1e8 if float_mcap_yi is not None else None),
            "list_date": "",
            "price": row.get("price"),
            "exchange": prefix,
            "symbol": full,
            "source": "tencent_quote_fallback",
            "data_quality": "partial_fallback",
        }

    def _as_ok(payload: dict) -> dict:
        src = payload.pop("source", None)
        if payload.get("data_quality") == "partial_fallback":
            payload.setdefault("partial", True)
            payload.setdefault("warning", "fallback only provides a subset of stock_info fields")
        return result_ok(source=src, **payload)

    for src in order:
        if src == "eastmoney":
            try:
                out = _from_em()
                return _as_ok(out)
            except Exception:
                pass
            continue
        if src == "tencent":
            try:
                out = _from_tencent()
                return _as_ok(out)
            except Exception:
                pass

    return result_err(
        "stock_info unavailable",
        source="stock_info",
        code=pure,
        name="",
        industry="",
        total_shares=None,
        float_shares=None,
        mcap=None,
        float_mcap=None,
        list_date="",
        price=None,
        exchange=prefix,
        symbol=full,
    )


@operation("query")
def stock_info(code):
    """A/港/美轻量档案；始终返回 ``result_ok`` / ``result_err`` 信封。"""
    try:
        return _stock_info(code)
    except Exception as exc:
        message = str(exc)
        return result_err(
            message,
            source="stock_info",
            code=str(code),
            error_code=(
                message.split(":", 1)[0]
                if message.startswith(("unsupported_market:", "unsupported_asset:"))
                else "invalid_code"
            ),
        )


def _normalize_report_type(report_type):
    key = "lrb" if report_type is None else str(report_type).strip()
    low = key.lower()
    normalized = _REPORT_TYPE_ALIASES.get(low) or _REPORT_TYPE_ALIASES.get(key)
    if not normalized:
        raise ValueError(
            "unknown report_type %r; choose lrb/llb/fzb or a documented alias" % report_type
        )
    return normalized


def _normalize_period(period):
    key = "annual" if period is None else str(period).strip()
    normalized = _PERIOD_ALIASES.get(key.lower()) or _PERIOD_ALIASES.get(key)
    if not normalized:
        raise ValueError("unknown period %r; choose annual/all/quarter/cumulative" % period)
    return normalized


def _pivot_line_items(rows, *, date_key, name_key, amount_key, num, extra_keys=None):
    """长表科目行 → 按报告期宽表 list[dict]（与 A 股 financial_report 形态对齐）。"""
    by_date = {}
    meta = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        raw_d = r.get(date_key) or r.get("STD_REPORT_DATE") or ""
        d = str(raw_d)[:10]
        if not d or d.startswith("None"):
            continue
        name = r.get(name_key)
        if not name:
            continue
        bucket = by_date.setdefault(d, {})
        bucket[str(name)] = r.get(amount_key)
        if d not in meta:
            m = {}
            for k in extra_keys or ():
                if r.get(k) not in (None, ""):
                    m[k] = r.get(k)
            if m:
                meta[d] = m
    periods = sorted(by_date.keys(), reverse=True)[: max(1, int(num or 8))]
    out = []
    for d in periods:
        rec = {"报告期": d}
        if d in meta:
            # 中文友好可选字段
            if meta[d].get("CURRENCY"):
                rec["币种"] = meta[d]["CURRENCY"]
            if meta[d].get("SECURITY_NAME_ABBR"):
                rec["名称"] = meta[d]["SECURITY_NAME_ABBR"]
            if meta[d].get("REPORT_TYPE"):
                rec["报告类型"] = meta[d]["REPORT_TYPE"]
            if meta[d].get("REPORT"):
                rec["报告标签"] = meta[d]["REPORT"]
        rec.update(by_date[d])
        out.append(rec)
    return out


def _financial_report_a(code, report_type="lrb", num=8, period="annual"):
    """AKShare full native statements; retain every field, including bank-specific accounts."""
    from scutio_data._providers.akshare.client import fetch

    _, prefix, pure = require_a_share(code, "financial_report")
    annual = _normalize_period(period) == "annual"
    functions = {
        "lrb": ("stock_profit_sheet_by_yearly_em", "stock_profit_sheet_by_report_em"),
        "fzb": ("stock_balance_sheet_by_yearly_em", "stock_balance_sheet_by_report_em"),
        "llb": ("stock_cash_flow_sheet_by_yearly_em", "stock_cash_flow_sheet_by_report_em"),
    }[_normalize_report_type(report_type)]
    function = functions[0 if annual else 1]
    rows = fetch(function, symbol=(prefix + pure).upper())
    output = []
    for raw in rows:
        if str(raw.get("SECURITY_CODE") or "") != pure:
            raise ValueError("A financial statement identity mismatch")
        validate_identity(raw, prefix + pure, fields=("SECURITY_CODE", "SECUCODE", "symbol"))
        day = str(raw.get("REPORT_DATE") or "")[:10]
        from datetime import date

        date.fromisoformat(day)
        if annual and not day.endswith("-12-31"):
            continue
        record = dict(raw)
        record["报告期"] = day
        # Source-native IDs are unique. No display-title deduplication or schema reduction.
        record["_line_items"] = [
            {
                "field_id": key,
                "key": key,
                "value": value,
                "yoy": raw.get(key + "_YOY"),
                "yoy_unit": "pct",
            }
            for key, value in raw.items()
            if not key.endswith("_YOY")
        ]
        output.append(record)
    output.sort(key=lambda row: row["报告期"], reverse=True)
    if len({row["报告期"] for row in output}) != len(output):
        raise ValueError("ambiguous duplicate statement periods")
    return output[:num]


def _financial_report_hk(pure, report_type="lrb", num=8, period="annual"):
    from scutio_data._providers.akshare.client import fetch

    rows = fetch(
        "stock_financial_hk_report_em",
        stock=pure,
        symbol=_HK_REPORT_NAME[report_type],
        indicator="年度" if period == "annual" else "报告期",
    )
    if any(str(row.get("SECURITY_CODE", pure)).zfill(5) != pure for row in rows):
        raise ValueError("HK financial statement identity mismatch")
    return _pivot_line_items(
        rows,
        date_key="REPORT_DATE",
        name_key="STD_ITEM_NAME",
        amount_key="AMOUNT",
        num=num,
        extra_keys=("SECURITY_NAME_ABBR", "CURRENCY"),
    )


def _financial_report_us(pure, report_type="lrb", num=8, period="annual"):
    from scutio_data._providers.akshare.client import fetch

    indicators = {
        "annual": ["年报"],
        "all": ["年报"],
        "quarter": ["单季报"],
        "cumulative": ["累计季报"],
    }[period]
    # Balance sheets are point-in-time: include H1/Q3/year-end snapshots.
    if period == "quarter" and report_type == "fzb":
        indicators = ["单季报", "累计季报", "年报"]
    rows = []
    for indicator in indicators:
        rows.extend(
            fetch(
                "stock_financial_us_report_em",
                stock=pure.replace(".", "_"),
                symbol=_US_REPORT_NAME[report_type],
                indicator=indicator,
            )
        )
    if any(
        str(row.get("SECURITY_CODE", pure)).replace("_", ".") != pure.replace("_", ".")
        for row in rows
    ):
        raise ValueError("US financial statement identity mismatch")
    if period == "quarter" and report_type != "fzb":
        rows = [
            row
            for row in rows
            if "Q6" not in str(row.get("REPORT")) and "Q9" not in str(row.get("REPORT"))
        ]
    return _pivot_line_items(
        rows,
        date_key="REPORT_DATE",
        name_key="ITEM_NAME",
        amount_key="AMOUNT",
        num=num,
        extra_keys=("SECURITY_NAME_ABBR", "CURRENCY", "REPORT_TYPE", "REPORT"),
    )


@operation("history")
def financial_report(
    code, report_type="lrb", num=8, period="annual", *, detail="full", sources=None
):
    """三表（A/港/美）。

    返回 **dict 信封** ``result_list``：``items`` 为每期一条宽表（至少含 ``报告期``）。
    港/美须显式 ``hk00700`` / ``usAAPL``。

    - ``report_type``: ``lrb`` 利润表 / ``llb`` 现金流 / ``fzb`` 资产负债表
      （``zcfzb``、中文名等会映射）
    - ``num``: 最近期数
    - ``period``: ``annual``（默认）；港可用 ``all``；美可用 ``quarter`` / ``cumulative``
    """
    try:
        mkt, prefix, pure = require_company(code, "financial_report")
        n = 8 if num is None else int(num)
        if n < 1:
            raise ValueError("num must be >= 1")
        report_type_norm = _normalize_report_type(report_type)
        period_norm = _normalize_period(period)
        from scutio_data._providers.hithink import client as hithink

        if detail not in ("full", "summary"):
            raise ValueError("detail must be full or summary")
        fallback_reason = None
        public_sources = ("akshare", "akshare_eastmoney", "public")
        if (
            detail == "summary"
            and mkt == "a"
            and ("hithink" in sources if sources is not None else hithink.preferred(code))
        ):
            try:
                return hithink.financial_summary(code, report_type_norm, n, period_norm)
            except Exception as exc:
                fallback_reason = str(exc)
                if sources is not None and not any(src in sources for src in public_sources):
                    return result_list_err(fallback_reason, source="hithink", detail="summary")
        elif sources is not None and not any(src in sources for src in public_sources):
            raise ValueError("requested source does not support this report view/market")
        if mkt == "a":
            if period_norm not in ("annual", "all"):
                raise ValueError("period %r is unsupported for A-share financial_report" % period)
            items = _financial_report_a(
                code, report_type=report_type_norm, num=n, period=period_norm
            )
            src = "financial_report_a"
        elif prefix == "hk" or mkt == "hk":
            if period_norm not in ("annual", "all"):
                raise ValueError("period %r is unsupported for HK financial_report" % period)
            items = _financial_report_hk(
                pure, report_type=report_type_norm, num=n, period=period_norm
            )
            src = "financial_report_hk"
        elif prefix == "us" or mkt == "us":
            if period_norm == "all":
                period_norm = "annual"
            if period_norm not in ("annual", "quarter", "cumulative"):
                raise ValueError("period %r is unsupported for US financial_report" % period)
            items = _financial_report_us(
                pure, report_type=report_type_norm, num=n, period=period_norm
            )
            src = "financial_report_us"
        else:
            return result_list_err(
                "unsupported market for financial_report: %r" % code,
                source="financial_report",
            )
        missing_currency = mkt in ("hk", "us") and any(not row.get("币种") for row in items)
        warnings = []
        if len(items) < n:
            warnings.append(
                "Only %d of %d requested report periods are available" % (len(items), n)
            )
        if missing_currency:
            warnings.append(
                "Source did not provide report currency; verify it before comparing amounts"
            )
        return result_list(
            items,
            source=src,
            adapter="akshare",
            provider="eastmoney",
            report_type=report_type_norm,
            period=period_norm,
            detail="full",
            requested_detail=detail,
            field_schema="source_native",
            fallback_reason=fallback_reason,
            requested_count=n,
            returned_count=len(items),
            partial=len(items) < n or missing_currency,
            missing_fields=["currency"] if missing_currency else [],
            warning="; ".join(warnings) or None,
        )
    except Exception as exc:
        return result_list_err(str(exc), source="financial_report")


@operation("history")
def stock_materials(code, name=None):
    """个股资料长文（分类目录或某一分类正文）。**仅 A 股**。

    与 ``stock_info``（结构化轻量档案）不同：本函数返回**文本资料**，
    不是股本/市值卡片。

    ``name`` 为空返回 ``result_list`` 分类目录；否则返回 ``result_ok(text=...)``。
    """
    try:
        _, _, pure = require_a_share(code, "stock_materials")
        from scutio_data._providers.akshare.market import profile

        categories = {"公司概况": "机构简介", "主营业务": "主营业务", "经营范围": "经营范围"}
        if name is None:
            return result_list(
                [{"name": name} for name in categories],
                source="akshare_cninfo",
                code=pure,
                mode="categories",
            )
        if str(name) not in categories:
            raise ValueError("unsupported category; choose 公司概况/主营业务/经营范围")
        row = profile(code)
        text = row.get(categories[str(name)])
        if not text:
            raise ValueError("company profile category unavailable")
        return result_ok(
            source="akshare_cninfo",
            code=pure,
            category=str(name),
            text=text,
        )
    except Exception as exc:
        message = str(exc)
        payload = {
            "code": str(code),
            "error_code": (
                message.split(":", 1)[0]
                if message.startswith(("unsupported_market:", "unsupported_asset:"))
                else "stock_materials_error"
            ),
        }
        if name is None:
            return result_list_err(message, source="stock_materials", **payload)
        return result_err(message, source="stock_materials", text=None, **payload)
