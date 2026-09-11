"""Allowed adapters and their verified upstream scope (AKShare 1.18.94 source).

Limits apply to adapter starts, not the unobservable HTTP requests inside AKShare.
Heavy scans share their provider slots and additionally allow one same-family scan.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Adapter:
    provider: str
    family: str
    heavy_scan: bool = False


ADAPTERS = {
    "bond_zh_us_rate": Adapter("eastmoney", "bond_zh_us_rate"),
    "fund_etf_hist_em": Adapter("eastmoney", "fund_etf_hist_em"),
    "fund_etf_hist_sina": Adapter("sina", "fund_etf_hist_sina"),
    "futures_foreign_commodity_realtime": Adapter("sina", "futures_foreign_commodity_realtime"),
    "index_stock_cons_csindex": Adapter("csindex", "index_stock_cons_csindex"),
    "index_stock_cons_sina": Adapter("sina", "index_stock_cons_sina"),
    "index_stock_cons_weight_csindex": Adapter("csindex", "index_stock_cons_weight_csindex"),
    "index_zh_a_hist": Adapter("eastmoney", "index_zh_a_hist"),
    "macro_china_consumer_goods_retail": Adapter("eastmoney", "macro_china_consumer_goods_retail"),
    "macro_china_cpi": Adapter("eastmoney", "macro_china_cpi"),
    "macro_china_cpi_yearly": Adapter("jin10", "macro_china_cpi_yearly"),
    "macro_china_enterprise_boom_index": Adapter("eastmoney", "macro_china_enterprise_boom_index"),
    "macro_china_exports_yoy": Adapter("jin10", "macro_china_exports_yoy"),
    "macro_china_fdi": Adapter("eastmoney", "macro_china_fdi"),
    "macro_china_fx_gold": Adapter("eastmoney", "macro_china_fx_gold"),
    "macro_china_fx_reserves_yearly": Adapter("jin10", "macro_china_fx_reserves_yearly"),
    "macro_china_gdp": Adapter("eastmoney", "macro_china_gdp"),
    "macro_china_gdzctz": Adapter("eastmoney", "macro_china_gdzctz"),
    "macro_china_gyzjz": Adapter("eastmoney", "macro_china_gyzjz"),
    "macro_china_hgjck": Adapter("eastmoney", "macro_china_hgjck"),
    "macro_china_imports_yoy": Adapter("jin10", "macro_china_imports_yoy"),
    "macro_china_industrial_production_yoy": Adapter(
        "jin10", "macro_china_industrial_production_yoy"
    ),
    "macro_china_lpr": Adapter("eastmoney", "macro_china_lpr"),
    "macro_china_m2_yearly": Adapter("jin10", "macro_china_m2_yearly"),
    "macro_china_money_supply": Adapter("eastmoney", "macro_china_money_supply"),
    "macro_china_national_tax_receipts": Adapter("eastmoney", "macro_china_national_tax_receipts"),
    "macro_china_new_financial_credit": Adapter("eastmoney", "macro_china_new_financial_credit"),
    "macro_china_non_man_pmi": Adapter("jin10", "macro_china_non_man_pmi"),
    "macro_china_pmi": Adapter("eastmoney", "macro_china_pmi"),
    "macro_china_pmi_yearly": Adapter("jin10", "macro_china_pmi_yearly"),
    "macro_china_ppi": Adapter("eastmoney", "macro_china_ppi"),
    "macro_china_ppi_yearly": Adapter("jin10", "macro_china_ppi_yearly"),
    "macro_china_reserve_requirement_ratio": Adapter(
        "eastmoney", "macro_china_reserve_requirement_ratio"
    ),
    "macro_china_shrzgm": Adapter("mofcom", "macro_china_shrzgm"),
    "macro_china_xfzxx": Adapter("eastmoney", "macro_china_xfzxx"),
    "macro_usa_cb_consumer_confidence": Adapter("jin10", "macro_usa_cb_consumer_confidence"),
    "macro_usa_cpi_monthly": Adapter("jin10", "macro_usa_cpi_monthly"),
    "macro_usa_cpi_yoy": Adapter("eastmoney", "macro_usa_cpi_yoy"),
    "macro_usa_gdp_monthly": Adapter("jin10", "macro_usa_gdp_monthly"),
    "macro_usa_ism_non_pmi": Adapter("jin10", "macro_usa_ism_non_pmi"),
    "macro_usa_ism_pmi": Adapter("jin10", "macro_usa_ism_pmi"),
    "macro_usa_michigan_consumer_sentiment": Adapter(
        "jin10", "macro_usa_michigan_consumer_sentiment"
    ),
    "macro_usa_non_farm": Adapter("jin10", "macro_usa_non_farm"),
    "macro_usa_retail_sales": Adapter("jin10", "macro_usa_retail_sales"),
    "macro_usa_unemployment_rate": Adapter("jin10", "macro_usa_unemployment_rate"),
    "news_economic_baidu": Adapter("baidu", "news_economic_baidu"),
    "news_trade_notify_suspend_baidu": Adapter("baidu", "news_trade_notify_suspend_baidu"),
    "rate_interbank": Adapter("eastmoney", "rate_interbank"),
    "stock_balance_sheet_by_report_em": Adapter("eastmoney", "financial_statements_a"),
    "stock_board_industry_name_em": Adapter("eastmoney", "stock_board_industry_name_em", True),
    "stock_cash_flow_sheet_by_report_em": Adapter("eastmoney", "financial_statements_a"),
    "stock_dividend_cninfo": Adapter("cninfo", "stock_dividend_cninfo"),
    "stock_dzjy_mrmx": Adapter("eastmoney", "stock_dzjy_mrmx", True),
    "stock_fhps_detail_em": Adapter("eastmoney", "stock_fhps_detail_em"),
    "stock_financial_hk_report_em": Adapter("eastmoney", "stock_financial_hk_report_em"),
    "stock_financial_us_report_em": Adapter("eastmoney", "stock_financial_us_report_em"),
    "stock_ggcg_em": Adapter("eastmoney", "stock_ggcg_em", True),
    "stock_gpzy_pledge_ratio_em": Adapter("eastmoney", "stock_gpzy_pledge_ratio_em", True),
    "stock_gpzy_profile_em": Adapter("eastmoney", "stock_gpzy_profile_em", True),
    "stock_gsrl_gsdt_em": Adapter("eastmoney", "stock_gsrl_gsdt_em"),
    "stock_hk_company_profile_em": Adapter("eastmoney", "stock_hk_company_profile_em"),
    "stock_hk_daily": Adapter("sina", "stock_hk_daily"),
    "stock_hk_hist": Adapter("eastmoney", "stock_hk_hist"),
    "stock_hsgt_hist_em": Adapter("eastmoney", "stock_hsgt_hist_em"),
    "stock_individual_fund_flow": Adapter("eastmoney", "stock_individual_fund_flow"),
    "stock_individual_info_em": Adapter("eastmoney", "stock_individual_info_em"),
    "stock_info_global_cls": Adapter("cls", "stock_info_global_cls"),
    "stock_info_global_em": Adapter("eastmoney", "stock_info_global_em"),
    "stock_irm_cninfo": Adapter("cninfo", "stock_irm_cninfo", True),
    "stock_lhb_detail_em": Adapter("eastmoney", "stock_lhb_detail_em", True),
    "stock_lhb_jgmmtj_em": Adapter("eastmoney", "stock_lhb_jgmmtj_em", True),
    "stock_lhb_stock_detail_em": Adapter("eastmoney", "stock_lhb_stock_detail_em", True),
    "stock_margin_detail_bse": Adapter("bse", "stock_margin_detail_bse", True),
    "stock_margin_detail_sse": Adapter("sse", "stock_margin_detail_sse", True),
    "stock_margin_detail_szse": Adapter("szse", "stock_margin_detail_szse", True),
    "stock_market_activity_legu": Adapter("legu", "stock_market_activity_legu"),
    "stock_news_em": Adapter("eastmoney", "stock_news_em"),
    "stock_profile_cninfo": Adapter("cninfo", "stock_profile_cninfo"),
    "stock_profit_forecast_ths": Adapter("ths_public", "stock_profit_forecast_ths"),
    "stock_profit_sheet_by_report_em": Adapter("eastmoney", "financial_statements_a"),
    "stock_repurchase_em": Adapter("eastmoney", "stock_repurchase_em", True),
    "stock_research_report_em": Adapter("eastmoney", "stock_research_report_em"),
    "stock_restricted_release_queue_em": Adapter("eastmoney", "stock_restricted_release_queue_em"),
    "stock_sector_spot": Adapter("sina", "stock_sector_spot"),
    "stock_tfp_em": Adapter("eastmoney", "stock_tfp_em", True),
    "stock_us_daily": Adapter("sina", "stock_us_daily"),
    "stock_us_hist": Adapter("eastmoney", "stock_us_hist"),
    "stock_us_spot_em": Adapter("eastmoney", "stock_us_spot_em", True),
    "stock_value_em": Adapter("eastmoney", "stock_value_em"),
    "stock_yjkb_em": Adapter("eastmoney", "stock_yjkb_em", True),
    "stock_yysj_em": Adapter("eastmoney", "stock_yysj_em", True),
    "stock_zh_a_daily": Adapter("sina", "stock_zh_a_daily"),
    "stock_zh_a_disclosure_report_cninfo": Adapter(
        "cninfo", "stock_zh_a_disclosure_report_cninfo", True
    ),
    "stock_zh_a_gdhs_detail_em": Adapter("eastmoney", "stock_zh_a_gdhs_detail_em"),
    "stock_zh_a_hist": Adapter("eastmoney", "stock_zh_a_hist"),
    "stock_zh_a_hist_tx": Adapter("tencent", "stock_zh_a_hist_tx"),
    "stock_zh_a_spot": Adapter("sina", "stock_zh_a_spot", True),
    "stock_zh_a_spot_em": Adapter("eastmoney", "stock_zh_a_spot_em", True),
    "stock_zh_valuation_baidu": Adapter("baidu", "stock_zh_valuation_baidu"),
    "tool_trade_date_hist_sina": Adapter("sina", "tool_trade_date_hist_sina"),
}

ALLOWED = frozenset(ADAPTERS)
