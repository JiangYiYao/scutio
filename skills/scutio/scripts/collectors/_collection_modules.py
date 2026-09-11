"""按显式研究需求构造请求；不按期限或角色自动扩展模块。"""

from dataclasses import dataclass, field
from functools import partial
from typing import Callable

from scutio_data import (
    announcements,
    breadth,
    capital,
    feeds,
    fundamentals,
    macro,
    market,
    research,
    valuation,
)
from scutio_data.core import canonical_symbol, market_of, result_err

MODULES = (
    "profile",
    "quote",
    "bars",
    "income",
    "cashflow",
    "balance",
    "filings",
    "reports",
    "news",
    "consensus",
    "revisions",
    "valuation",
    "valuation_history",
    "fund_flow",
    "corporate_actions",
    "breadth",
    "peers",
    "rates",
    "bonds",
    "fx",
    "commodities",
    "calendar",
)
GLOBAL_MODULES = {"rates", "bonds", "fx", "commodities", "calendar"}
STATEMENTS = {"income": "lrb", "cashflow": "llb", "balance": "fzb"}


@dataclass
class Request:
    fetch: Callable[..., dict]
    parameters: dict = field(default_factory=dict)
    input_module: str | None = None
    input_argument: str | None = None


def normalize_modules(modules) -> list[str]:
    if isinstance(modules, str):
        modules = [part.strip() for part in modules.split(",")]
    if not isinstance(modules, (list, tuple)) or not modules:
        raise ValueError("modules must explicitly select at least one capability")
    for name in modules:
        if not isinstance(name, str) or not (
            name in MODULES or name.startswith("macro:") and name[6:] in macro.ALL_MACRO_SERIES
        ):
            raise ValueError(
                "unknown module; choose from " + ", ".join(MODULES) + " or macro:<series>"
            )
    return list(dict.fromkeys(modules))


def normalize_peers(peers, target: str | None) -> list[dict]:
    if not isinstance(peers, list) or not peers:
        raise ValueError("peers requires a nonempty list of code/relation/basis objects")
    rows, seen = [], set()
    for row in peers:
        if not isinstance(row, dict):
            raise ValueError("peer mapping must contain objects")
        code = canonical_symbol(row.get("code"))
        if code == target or code in seen:
            raise ValueError("peer codes must be unique and differ from target")
        if any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in ("relation", "basis")
        ):
            raise ValueError("peer relation and basis are required")
        seen.add(code)
        rows.append(
            {
                "code": code,
                "relation": row["relation"].strip(),
                "basis": row["basis"].strip(),
                "source_ref": str(row.get("source_ref") or ""),
            }
        )
    return rows


def _peer_quotes(mapping: list[dict]) -> dict:
    payload = market.security_quote([row["code"] for row in mapping])
    return {
        **payload,
        "peer_mapping": mapping,
        "note": "逐标的保留报价源时点；关系来自输入材料，不计算跨市场同步收益。",
    }


def build_requests(
    symbol, modules, *, depth, period, filing_kind, peers=None
) -> dict[str, Request]:
    count = 4 if depth == "light" else 8
    report_pages = 1 if depth == "light" else 2
    requests = {}
    for name in modules:
        if name.startswith("macro:"):
            params = {"name": name[6:], "limit": 12 if depth == "light" else 36}
            requests[name] = Request(partial(macro.macro_series, **params), params)
        elif name in GLOBAL_MODULES:
            requests[name] = Request(
                {
                    "rates": macro.rates_snapshot,
                    "bonds": macro.bond_yields_cn_us,
                    "fx": macro.fx_usdcny,
                    "commodities": macro.commodities_spot,
                    "calendar": macro.economic_calendar,
                }[name]
            )
        elif name == "peers":
            mapping = normalize_peers(peers, symbol)
            requests[name] = Request(partial(_peer_quotes, mapping), {"peers": mapping})
        elif symbol is None:
            raise ValueError("code is required for module " + name)
        elif (
            name
            in (
                "reports",
                "consensus",
                "revisions",
                "valuation_history",
                "fund_flow",
                "corporate_actions",
            )
            and market_of(symbol) != "a"
        ):
            requests[name] = Request(
                partial(
                    result_err, "unsupported_market", source=name, error_code="unsupported_market"
                )
            )
        elif name in STATEMENTS:
            params = {"period": period, "num": count}
            requests[name] = Request(
                partial(
                    fundamentals.financial_report, symbol, STATEMENTS[name], count, period=period
                ),
                params,
            )
        elif name == "profile":
            requests[name] = Request(partial(fundamentals.stock_info, symbol))
        elif name == "quote":
            requests[name] = Request(partial(market.security_quote, [symbol]))
        elif name == "bars":
            params = {"frequency": "D", "count": 80 if depth == "light" else 160, "adjust": "none"}
            requests[name] = Request(partial(market.security_bars, symbol, **params), params)
        elif name == "filings":
            params = {"kind": filing_kind, "page_size": count}
            requests[name] = Request(
                partial(announcements.periodic_reports, symbol, **params), params
            )
        elif name == "reports":
            params = {"max_pages": report_pages}
            requests[name] = Request(partial(research.stock_reports, symbol, **params), params)
        elif name == "news":
            params = {"page_size": count}
            requests[name] = Request(partial(feeds.stock_news, symbol, **params), params)
        elif name == "consensus":
            requests[name] = Request(partial(research.consensus_forecast, symbol))
        elif name == "revisions":
            params = {"max_pages": report_pages}
            requests[name] = Request(
                partial(research.consensus_revisions, symbol, **params),
                params,
                "reports",
                "reports",
            )
        elif name == "valuation":
            requests[name] = Request(
                partial(valuation.valuation_snapshot, symbol), {}, "quote", "quote_env"
            )
        elif name == "valuation_history":
            requests[name] = Request(partial(valuation.valuation_history, symbol))
        elif name == "fund_flow":
            requests[name] = Request(partial(capital.stock_fund_flow_120d, symbol))
        elif name == "corporate_actions":
            params = {"page_size": count}
            requests[name] = Request(partial(capital.corporate_actions, symbol, **params), params)
        elif name == "breadth":
            requests[name] = Request(partial(breadth.market_breadth, market_of(symbol)))
    return requests
