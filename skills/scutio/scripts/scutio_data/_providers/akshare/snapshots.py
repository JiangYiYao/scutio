"""Full-market snapshots use the common exact-request cache and source quota."""

from scutio_data._providers.akshare import client as akshare_source

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
    return akshare_source.fetch(
        function, _timeout_seconds=_timeout_seconds, _snapshot=True, **params
    )
