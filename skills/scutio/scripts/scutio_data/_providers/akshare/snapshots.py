"""Bounded full-market AKShare snapshots shared across stock queries."""

from __future__ import annotations

import hashlib
import json
import time
from importlib.metadata import version

from scutio_data._providers.akshare import client as akshare_source
from scutio_data._runtime.cache import write_api_cache
from scutio_data._runtime.storage import read_json
from scutio_data.paths import cache_dir

TTL_SECONDS = 3600


SNAPSHOT_FUNCTIONS = frozenset(
    (
        "stock_lhb_detail_em",
        "stock_lhb_stock_detail_em",
        "stock_lhb_jgmmtj_em",
        "stock_tfp_em",
        "stock_yysj_em",
        "stock_ggcg_em",
        "stock_repurchase_em",
        "stock_dzjy_mrmx",
        "stock_gpzy_profile_em",
        "stock_gpzy_pledge_ratio_em",
        "stock_margin_detail_sse",
        "stock_margin_detail_szse",
        "stock_margin_detail_bse",
        "tool_trade_date_hist_sina",
    )
)


def fetch_snapshot(function, *, _timeout_seconds=None, **params):
    if function not in SNAPSHOT_FUNCTIONS:
        raise ValueError("unsupported bulk snapshot")
    identity = {
        "function": function,
        "params": params,
        "akshare_version": version("akshare"),
        "schema": 1,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = cache_dir() / "api" / "akshare" / (digest + ".json")
    cached = read_json(path)
    now = time.time()
    stamp = cached.get("retrieved_at")
    rows = cached.get("items")
    if (
        cached.get("identity") == identity
        and isinstance(stamp, (int, float))
        and 0 <= now - stamp < TTL_SECONDS
        and isinstance(rows, list)
        and all(isinstance(row, dict) for row in rows)
    ):
        return rows, {
            "snapshot_retrieved_at": stamp,
            "snapshot_cached": True,
            "snapshot_ttl_seconds": TTL_SECONDS,
        }
    # Failed refreshes never turn expired data into a fresh success.
    rows = (
        akshare_source.fetch(function, **params)
        if _timeout_seconds is None
        else akshare_source.fetch(function, _timeout_seconds=_timeout_seconds, **params)
    )
    stamp = time.time()
    try:
        write_api_cache(
            path, {"identity": identity, "retrieved_at": stamp, "items": rows}, ttl=TTL_SECONDS
        )
    except OSError:
        pass  # Read-only cache locations must not discard a successful fetch.
    return rows, {
        "snapshot_retrieved_at": stamp,
        "snapshot_cached": False,
        "snapshot_ttl_seconds": TTL_SECONDS,
    }
