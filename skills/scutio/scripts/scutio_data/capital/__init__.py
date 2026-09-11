"""资金、股东与公司行动的公开入口。"""

from __future__ import annotations

from scutio_data import source_pref as source_pref
from scutio_data._providers.eastmoney_capital import concept_blocks as concept_blocks
from scutio_data._runtime.parsing import finite_number, source_a_code, source_date
from scutio_data._runtime.results import (
    envelope_items,
    result_err,
    result_list,
    result_list_err,
    result_ok,
)
from scutio_data._runtime.symbols import market_of, require_a_share
from scutio_data._runtime.timeouts import operation
from scutio_data.capital import dividends
from scutio_data.capital.dividends import dividend_history
from scutio_data.capital.flows import MUTUAL_TYPE as MUTUAL_TYPE
from scutio_data.capital.flows import mutual_connect_daily as mutual_connect_daily
from scutio_data.capital.flows import northbound_daily as northbound_daily
from scutio_data.capital.flows import southbound_daily as southbound_daily
from scutio_data.capital.flows import stock_fund_flow_120d as stock_fund_flow_120d

__all__ = [
    "MUTUAL_TYPE",
    "dragon_tiger_board",
    "daily_dragon_tiger",
    "southbound_daily",
    "northbound_daily",
    "mutual_connect_daily",
    "concept_blocks",
    "stock_fund_flow_120d",
    "margin_trading",
    "block_trade",
    "holder_num_change",
    "dividend_history",
    "share_repurchases",
    "shareholder_changes",
    "pledge_status",
    "corporate_actions",
    "ownership_filings",
    "industry_comparison",
]


def margin_trading(code, page_size=30, *, end_date=None, lookback_days=90):
    """AKShare 按日期市场快照组成个股历史；返回范围与缺失字段。"""
    try:
        require_a_share(code, "margin_trading")
        from scutio_data.capital.history import margin_history

        return margin_history(code, page_size, end_date=end_date, lookback_days=lookback_days)
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="margin_trading",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


def block_trade(code, page_size=20, *, end_date=None, lookback_days=365):
    """AKShare 按日期市场快照组成个股历史；返回范围与缺失字段。"""
    try:
        require_a_share(code, "block_trade")
        from scutio_data.capital.history import block_history

        return block_history(code, page_size, end_date=end_date, lookback_days=lookback_days)
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="block_trade",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


@operation("history")
def holder_num_change(code, page_size=10):
    """股东户数历史；户均总持股与户均流通持股分别标注。"""
    try:
        _, _, pure = require_a_share(code, "holder_num_change")
        from scutio_data._providers.akshare.client import fetch

        rows = fetch("stock_zh_a_gdhs_detail_em", symbol=pure)
        items = []
        for row in rows:
            if source_a_code(row.get("代码")) != pure:
                raise ValueError("AKShare holder identity mismatch")
            items.append(
                {
                    "date": source_date(row["股东户数统计截止日"]),
                    "holder_num": finite_number(row.get("股东户数-本次")),
                    "change_num": finite_number(row.get("股东户数-增减")),
                    "change_ratio": finite_number(row.get("股东户数-增减比例")),
                    "avg_shares": None,
                    "avg_total_shares": finite_number(row.get("户均持股数量")),
                }
            )
        items.sort(key=lambda row: row["date"], reverse=True)
        return result_list(
            items[: max(1, int(page_size))],
            source="holder_num_change",
            adapter="akshare",
            partial=True,
            coverage={"avg_float_shares": False, "avg_total_shares": True},
            warning="AKShare exposes average total shares, not average freely tradable shares",
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="holder_num_change",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


def share_repurchases(code, page_size=30):
    """A 股结构化回购计划/实施进度。AKShare 全市场快照按个股筛选。"""
    try:
        _, _, pure = require_a_share(code, "share_repurchases")
        from scutio_data._providers.akshare.snapshots import fetch_snapshot

        rows, snapshot = fetch_snapshot("stock_repurchase_em")
        rows = [row for row in rows if source_a_code(row.get("股票代码")) == pure]
        rows.sort(
            key=lambda row: (
                source_date(row.get("最新公告日期")),
                source_date(row.get("回购起始时间")),
            ),
            reverse=True,
        )
        rows = rows[: max(1, int(page_size))]
        items = []
        for row in rows:
            actual_amount = finite_number(row.get("已回购金额"))
            plan_low = finite_number(row.get("计划回购金额区间-下限"))
            plan_high = finite_number(row.get("计划回购金额区间-上限"))
            overwritten_plan = (
                row.get("实施进度") == "完成实施"
                and actual_amount is not None
                and plan_low == plan_high == actual_amount
            )
            items.append(
                {
                    "code": str(row.get("股票代码") or pure).zfill(6),
                    "name": row.get("股票简称") or "",
                    "latest_price": finite_number(row.get("最新价")),
                    "plan_price_cap": finite_number(row.get("计划回购价格区间")),
                    "plan_shares_low": finite_number(row.get("计划回购数量区间-下限")),
                    "plan_shares_high": finite_number(row.get("计划回购数量区间-上限")),
                    "plan_amount_low": None if overwritten_plan else plan_low,
                    "plan_amount_high": None if overwritten_plan else plan_high,
                    "plan_amount_available": not overwritten_plan
                    and plan_low is not None
                    and plan_high is not None,
                    "start_date": source_date(row.get("回购起始时间")),
                    "progress": row.get("实施进度"),
                    "actual_price_low": finite_number(row.get("已回购股份价格区间-下限")),
                    "actual_price_high": finite_number(row.get("已回购股份价格区间-上限")),
                    "actual_shares": finite_number(row.get("已回购股份数量")),
                    "actual_amount": actual_amount,
                    "updated_date": source_date(row.get("最新公告日期")),
                    "source": "eastmoney_repurchase",
                }
            )
        return result_list(
            items,
            source="eastmoney_repurchase",
            market="a",
            code=pure,
            adapter="akshare",
            partial=any(not item["plan_amount_available"] for item in items),
            warning=(
                "Original plan amounts are missing or appear replaced by final spend; "
                "those bounds are unavailable. Consult the original authorisation for plan amounts."
                if any(not item["plan_amount_available"] for item in items)
                else None
            ),
            **snapshot,
        )
    except Exception as exc:
        return result_list_err(str(exc), source="share_repurchases")


def shareholder_changes(code, direction="all", page_size=50):
    """AKShare 增持/减持独立快照；单方向失败显式部分覆盖。"""
    try:
        require_a_share(code, "shareholder_changes")
        from scutio_data.capital.history import shareholder_history

        return shareholder_history(code, direction, page_size)
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="shareholder_changes",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


def pledge_status(code, page_size=20, *, end_date=None, lookback_days=730):
    """AKShare 按日期市场快照组成个股历史；返回范围与缺失字段。"""
    try:
        require_a_share(code, "pledge_status")
        from scutio_data.capital.history import pledge_history

        return pledge_history(code, page_size, end_date=end_date, lookback_days=lookback_days)
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="pledge_status",
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else None,
        )


@operation("batch")
def corporate_actions(code, page_size=30, preloaded=None):
    """公司行动聚合：回购、增减持、质押、分红。

    各腿保持原口径；某腿失败时 ``ok=True, partial=True``，不会用公告标题冒充
    结构化数值 backup。
    """
    try:
        market, _, pure = require_a_share(code, "corporate_actions")
    except Exception as exc:
        return result_err(
            str(exc),
            source="corporate_actions",
            market=None,
            code=str(code),
            error_code=str(exc).split(":", 1)[0]
            if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
            else "invalid_code",
        )
    try:
        supplied = dict(preloaded or {})
    except Exception as exc:
        return result_err(
            "invalid preloaded mapping: %s" % exc,
            source="corporate_actions",
            market="a",
            code=pure,
            error_code="invalid_preloaded",
        )
    loaders = {
        "repurchases": share_repurchases,
        "holder_changes": shareholder_changes,
        "pledges": pledge_status,
        "dividends": dividends.dividend_history,
    }
    legs = {}
    for name, loader in loaders.items():
        env = supplied[name] if name in supplied else loader(code, page_size=page_size)
        if not isinstance(env, dict):
            env = result_list_err(
                "invalid preloaded envelope for %s" % name,
                source="corporate_actions",
            )
        legs[name] = env
    errors = {name: env.get("error") for name, env in legs.items() if not env.get("ok")}
    usable = any(env.get("ok") for env in legs.values())
    payload = {name: envelope_items(env) for name, env in legs.items()}
    if not usable:
        return result_err(
            "; ".join("%s: %s" % item for item in errors.items()),
            source="corporate_actions",
            market="a",
            code=pure,
            errors=errors,
            **payload,
        )
    return result_ok(
        source="corporate_actions",
        market="a",
        code=pure,
        partial=bool(errors) or any(env.get("partial") for env in legs.values()),
        errors=errors,
        leg_status={
            name: {key: env.get(key) for key in ("ok", "partial", "warning", "source")}
            for name, env in legs.items()
        },
        **payload,
    )


@operation("history")
def ownership_filings(code, page_size=50):
    """美股 SEC 所有权相关申报（Forms 3/4/5、13D/G、144）。"""
    try:
        market = market_of(code)
        if market != "us":
            return result_list_err(
                "unsupported_market: ownership_filings currently supports US SEC only; HK optional",
                source="ownership_filings",
                market=market,
            )
        from scutio_data._providers import sec

        ticker, cik, submissions = sec.load_submissions(code)
        recent = (submissions.get("filings") or {}).get("recent") or {}
        allowed = {
            "3",
            "3/A",
            "4",
            "4/A",
            "5",
            "5/A",
            "144",
            "144/A",
            "SC 13D",
            "SC 13D/A",
            "SC 13G",
            "SC 13G/A",
            "SCHEDULE 13D",
            "SCHEDULE 13D/A",
            "SCHEDULE 13G",
            "SCHEDULE 13G/A",
        }
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []
        dates = recent.get("filingDate") or []
        items = []
        for idx, form in enumerate(forms):
            if form not in allowed or idx >= len(accessions) or idx >= len(documents):
                continue
            document = documents[idx]
            accession = accessions[idx]
            items.append(
                {
                    "date": dates[idx] if idx < len(dates) else "",
                    "form": form,
                    "accession": accession,
                    "file_url": sec.document_url(cik, accession, document),
                    "primary_document": document,
                    "ticker": ticker,
                    "cik": cik,
                    "source": "sec_edgar_ownership",
                }
            )
            if len(items) >= max(1, int(page_size)):
                break
        return result_list(
            items,
            source="sec_edgar_ownership",
            market="us",
            ticker=ticker,
            cik=cik,
            coverage="SEC submissions.recent",
            note="13D/G 与 3/4/5 是披露申报；不是按股票聚合后的 13F 全机构持仓表",
        )
    except Exception as exc:
        return result_list_err(str(exc), source="ownership_filings")


@operation("query")
def industry_comparison(top_n=20):
    """AKShare 行业全表排序；东财与新浪分类不混合。"""
    from scutio_data._providers.akshare.client import fetch

    top_n = max(1, int(top_n))
    errors = {}
    for src in source_pref.ordered_sources("industry_comparison", ["eastmoney", "sina"]):
        try:
            if src == "eastmoney":
                raw = fetch("stock_board_industry_name_em")
                names = {
                    "板块名称": "name",
                    "板块代码": "code",
                    "上涨家数": "up_count",
                    "下跌家数": "down_count",
                    "领涨股票": "leader",
                    "领涨股票-涨跌幅": "leader_change",
                }
            else:
                raw = fetch("stock_sector_spot", indicator="新浪行业")
                names = {
                    "板块": "name",
                    "label": "code",
                    "公司家数": "constituent_count",
                    "股票名称": "leader",
                    "个股涨跌幅": "leader_change",
                }
            rows = []
            for row in raw:
                pct = finite_number(row.get("涨跌幅"))
                if pct is None:
                    continue
                item = {target: row.get(origin) for origin, target in names.items()}
                item.update(change_pct=pct)
                if src == "sina":
                    item.update(up_count=None, down_count=None)
                rows.append(item)
            if not rows:
                raise ValueError("industry data empty")
            rows.sort(key=lambda row: row["change_pct"], reverse=True)
            source_pref.mark_ok("industry_comparison", src, default_primary="eastmoney")
            return result_ok(
                source="industry_comparison" if src == "eastmoney" else "sina_industry",
                adapter="akshare",
                classification=src,
                total=len(rows),
                errors=errors,
                partial=src == "sina" or len(rows) < len(raw),
                discarded_count=len(raw) - len(rows),
                coverage={"advance_decline_counts": src == "eastmoney"},
                top=[dict(row, rank=i + 1) for i, row in enumerate(rows[:top_n])],
                bottom=[
                    dict(row, rank=i + 1, rank_from_bottom=i + 1)
                    for i, row in enumerate(list(reversed(rows))[:top_n])
                ],
            )
        except Exception as exc:
            errors[src] = str(exc)
            source_pref.mark_fail("industry_comparison", src, default_primary="eastmoney")
    return result_err(
        "; ".join(errors.values()),
        source="industry_comparison",
        errors=errors,
        top=[],
        bottom=[],
        total=0,
    )


def dragon_tiger_board(code, trade_date, look_back=30):
    """AKShare 个股龙虎榜与席位，按上榜原因区分不同统计窗口。"""
    from scutio_data.capital.dragon_tiger import board

    return board(code, trade_date, look_back)


def daily_dragon_tiger(trade_date=None, min_net_buy=None):
    """AKShare 全市场龙虎榜；缺失金额不补零。"""
    from scutio_data.capital.dragon_tiger import daily

    return daily(trade_date, min_net_buy)
