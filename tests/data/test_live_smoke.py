"""真实公网通路 smoke：确认线上接口可达且返回可用数据。

启用：
  SCUTIO_LIVE=1          本文件 HTTP / 多源行情

或：
  ./tests/run_tests.sh --live
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("SCUTIO_LIVE") != "1",
        reason="set SCUTIO_LIVE=1 (or tests/run_tests.sh --live) for public HTTP smoke",
    ),
]


@pytest.fixture(autouse=True)
def _pace_live_calls():
    """东财等源串行易断连：case 之间稍作间隔。"""
    yield

    time.sleep(0.8)


# 代表性标的：沪主板、深主板、ETF
CODE_SH = "600519"  # 贵州茅台
CODE_SZ = "000001"  # 平安银行
CODE_ETF = "510300"  # 沪深300ETF
CODE_IDX = "sh000001"  # 上证综指


def _price_ok(p: Any) -> bool:
    try:
        v = float(p)
    except (TypeError, ValueError):
        return False
    return 0 < v < 1e7


def _env_ok(env: Any) -> bool:
    if isinstance(env, dict) and "ok" in env:
        return bool(env.get("ok"))
    return env is not None


def _env_items(env: Any) -> list:
    if isinstance(env, dict) and "items" in env:
        return list(env.get("items") or [])
    if isinstance(env, (list, tuple)):
        return list(env)
    return []


def _env_err(env: Any) -> Any:
    if isinstance(env, dict):
        return env.get("error")
    return getattr(env, "error", None)


def _brief_quote(row: dict) -> str:
    return (
        f"name={row.get('name')!r} price={row.get('price')} "
        f"source={row.get('source')!r} symbol={row.get('symbol') or row.get('code')!r}"
    )


def test_live_tencent_quote(record_actual):
    """腾讯实时报价通路。"""
    from scutio_data.market import tencent_quote

    batch = tencent_quote([CODE_SH, CODE_SZ, CODE_IDX])
    assert isinstance(batch, dict) and batch, f"empty quotes: {batch!r}"

    # 茅台
    row = batch.get("sh" + CODE_SH) or batch.get(CODE_SH)
    assert row, f"missing {CODE_SH} in keys={list(batch)[:12]}"
    assert _price_ok(row.get("price")), row
    assert row.get("name"), row

    # 上证
    idx = batch.get(CODE_IDX) or batch.get("sh000001")
    assert idx and _price_ok(idx.get("price")), idx

    record_actual(
        "腾讯批量报价 OK · "
        + " | ".join(_brief_quote(batch[k]) for k in list(batch)[:4])
        + (f" · +{len(batch) - 4} more" if len(batch) > 4 else "")
    )


def test_live_security_quote_fallback(record_actual):
    """security_quote 多源（腾讯→Sina）真通路。"""
    from scutio_data.market import security_quote

    out = security_quote([CODE_SH, CODE_SZ])
    assert out.get("ok") is True, out
    quotes = out.get("quotes") or {}
    assert quotes, out
    # 至少一只有价
    priced = [
        q
        for q in quotes.values()
        if isinstance(q, dict) and _price_ok(q.get("price") or q.get("last_close"))
    ]
    assert priced, out
    record_actual(
        f"ok={out['ok']} sources_used={out.get('sources_used')} "
        f"n_quotes={len(quotes)} sample={_brief_quote(priced[0])}"
    )


def test_live_security_bars(record_actual):
    """日 K：公开门面通过 AKShare 选择免费源。"""
    from scutio_data.market import security_bars

    out = security_bars(CODE_SH, frequency="D", count=5)
    assert out.get("ok") is True, out
    bars = out.get("bars") or []
    assert len(bars) >= 1, out
    last = bars[-1]
    assert _price_ok(last.get("close")), last
    record_actual(
        f"ok source={out.get('source')!r} n={len(bars)} "
        f"last_dt={last.get('datetime')!r} close={last.get('close')} "
        f"errors={out.get('errors')}"
    )


def test_live_security_bars_index(record_actual):
    """指数日 K（上证）。"""
    from scutio_data.market import security_bars

    out = security_bars(CODE_IDX, frequency="D", count=3, index=True)
    assert out.get("ok") is True, out
    bars = out.get("bars") or []
    assert bars and _price_ok(bars[-1].get("close")), out
    record_actual(
        f"source={out.get('source')!r} n={len(bars)} "
        f"last_close={bars[-1].get('close')} dt={bars[-1].get('datetime')!r}"
    )


def test_live_eastmoney_stock_info(record_actual):
    """个股资料公开门面：AKShare 主路径或明确标记的报价子集。"""
    from scutio_data.fundamentals import stock_info

    info = stock_info(CODE_SH)
    assert isinstance(info, dict) and info.get("ok") is True, info
    assert info.get("source"), info
    assert info.get("name"), info
    if info.get("data_quality") == "partial_fallback":
        assert info.get("partial") is True, info
    record_actual(
        f"source={info.get('source')!r} name={info.get('name')!r} "
        f"price={info.get('price')} industry={info.get('industry')!r} "
        f"mcap={info.get('mcap')}"
    )


def test_live_valuation_snapshot(record_actual):
    """报价侧估值快照（不含一致预期）。"""
    from scutio_data.valuation import valuation_snapshot

    out = valuation_snapshot(CODE_SH)
    assert out.get("ok") is True, out
    assert _price_ok(out.get("price")), out
    assert out.get("name"), out
    record_actual(
        f"ok source={out.get('source')!r} name={out.get('name')!r} "
        f"price={out.get('price')} pe_ttm={out.get('pe_ttm')} pb={out.get('pb')}"
    )


def test_live_valuation_history(record_actual):
    """A 股历史估值主源或备用源。"""
    from scutio_data.valuation import valuation_history

    out = valuation_history(CODE_SH, include_series=False)
    assert out.get("ok") is True, out
    assert int(out.get("sample_count") or 0) > 100, out
    metrics = ((out.get("windows") or {}).get("all") or {}).get("metrics") or {}
    assert metrics.get("pe_ttm") or metrics.get("pb"), out
    record_actual(
        f"source={out.get('source')!r} n={out.get('sample_count')} "
        f"start={out.get('start_date')} end={out.get('end_date')} "
        f"pe_percentile={(metrics.get('pe_ttm') or {}).get('percentile')}"
    )


def test_live_cninfo_announcements(record_actual):
    """巨潮公告列表。"""
    from scutio_data.announcements import stock_announcements

    env = stock_announcements(CODE_SH, page_size=5)
    assert _env_ok(env), _env_err(env)
    rows = _env_items(env)
    assert len(rows) >= 1, f"empty announcements error={_env_err(env)}"
    first = rows[0]
    assert first.get("title"), first
    assert first.get("url"), first
    record_actual(
        f"n={len(rows)} source={(env.get('source') if isinstance(env, dict) else None)!r} "
        f"first_title={first.get('title')!r} date={first.get('date')!r}"
    )


def test_live_eastmoney_stock_news(record_actual):
    """东财个股新闻。"""
    from scutio_data.feeds import stock_news

    env = stock_news(CODE_SH, page_size=5)
    # 搜索偶发空结果：要求不报错；若有条目则 title 非空
    assert _env_ok(env), _env_err(env)
    rows = _env_items(env)
    if rows:
        assert rows[0].get("title"), rows[0]
    record_actual(
        f"ok n={len(rows)} source={(env.get('source') if isinstance(env, dict) else None)!r} "
        f"sample_title={(rows[0].get('title') if rows else None)!r} "
        f"error={_env_err(env)!r}"
    )


def test_live_cls_telegraph(record_actual):
    """财联社电报。"""
    from scutio_data.feeds import telegraph

    env = telegraph(page_size=5)
    assert _env_ok(env), _env_err(env)
    rows = _env_items(env)
    assert len(rows) >= 1, env
    assert rows[0].get("title") or rows[0].get("content"), rows[0]
    record_actual(
        f"n={len(rows)} first={(rows[0].get('title') or rows[0].get('content') or '')[:80]!r} "
        f"time={rows[0].get('time')!r}"
    )


def test_live_stock_fund_flow_120d(record_actual):
    from scutio_data.capital import stock_fund_flow_120d

    result = stock_fund_flow_120d(CODE_SH)
    assert result["ok"], result.get("error")
    rows = _env_items(result)
    assert rows and rows[-1].get("date")
    record_actual(f"AKShare n={len(rows)} last_date={rows[-1]['date']}")


def test_live_industry_comparison(record_actual):
    """行业涨跌榜双腿（东财 clist 易断，有限重试）。"""

    from scutio_data.capital import industry_comparison

    out = None
    for attempt in range(3):
        out = industry_comparison(top_n=5)
        assert isinstance(out, dict), out
        top = out.get("top") or out.get("gainers") or []
        bottom = out.get("bottom") or out.get("losers") or []
        if top or bottom:
            top0 = repr(top[0]) if top else "None"
            bottom0 = repr(bottom[0]) if bottom else "None"
            record_actual(
                f"ok={out.get('ok')} attempt={attempt + 1} top_n={len(top)} "
                f"bottom_n={len(bottom)} top0={top0} bottom0={bottom0}"
            )
            return
        time.sleep(2 * (attempt + 1))
    record_actual(f"FAILED after retries {out!r}"[:500])
    pytest.fail(f"industry_comparison failed: {out}")


def test_live_local_report_search(record_actual):
    """东财研报列表检索（真拉行业研报再本地打分）。"""
    from scutio_data.research import local_report_search

    env = local_report_search("人工智能", begin="2025-01-01", max_pages=1, limit=5)
    assert isinstance(env, dict), env
    assert env.get("ok") is True, env
    results = env.get("items") or []
    # 上游可能空：至少不抛；有结果则含 title
    if results:
        assert results[0].get("title") or results[0].get("infoCode"), results[0]
    record_actual(
        f"n={len(results)} "
        f"sample_title={(results[0].get('title') if results else None)!r} "
        f"matched={(results[0].get('_matched_terms') if results else None)!r}"
    )
