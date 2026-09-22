"""研报发现、一致预期与本地研究工具的公开入口。"""

from scutio_data._documents.report_paths import REPORT_CACHE_NOTE as REPORT_CACHE_NOTE
from scutio_data._documents.report_paths import (
    default_reports_dir as default_reports_dir,
)
from scutio_data._documents.reports import download_pdf as download_pdf
from scutio_data._documents.reports import report_abstract as report_abstract
from scutio_data._documents.reports import report_detail_url as report_detail_url
from scutio_data._documents.reports import report_page_detail as report_page_detail
from scutio_data._providers.eastmoney_research import broker_reports as broker_reports
from scutio_data._providers.eastmoney_research import (
    industry_reports as industry_reports,
)
from scutio_data.research.consensus import consensus_forecast as consensus_forecast
from scutio_data.research.consensus import consensus_revisions as consensus_revisions
from scutio_data.research.consensus import eps_forecast as eps_forecast
from scutio_data.research.discovery import stock_reports as stock_reports
from scutio_data.research.local import dedup_articles as dedup_articles
from scutio_data.research.local import list_local_reports as list_local_reports
from scutio_data.research.local import local_report_search as local_report_search
from scutio_data.research.local import local_stock_screen as local_stock_screen
from scutio_data.research.signals import forecast_price_changes as forecast_price_changes

__all__ = [
    "stock_reports",
    "download_pdf",
    "default_reports_dir",
    "list_local_reports",
    "industry_reports",
    "broker_reports",
    "report_detail_url",
    "report_page_detail",
    "report_abstract",
    "eps_forecast",
    "consensus_forecast",
    "consensus_revisions",
    "forecast_price_changes",
    "local_report_search",
    "local_stock_screen",
    "dedup_articles",
    "COMMON_INDUSTRY_CODES",
    "REPORT_CACHE_NOTE",
]


# Common Eastmoney industry codes (report list industryCode). Not exhaustive.
COMMON_INDUSTRY_CODES = {
    "游戏Ⅱ": "1046",
    "半导体": "1036",
    "软件开发": "737",
    "电池": "1033",
    "光伏设备": "1031",
    "风电设备": "1032",
    "通信设备": "448",
    "证券Ⅱ": "473",
    "银行Ⅱ": "475",
    "白酒Ⅱ": "1277",
    "化学制药": "465",
    "生物制品": "1044",
    "煤炭开采": "1250",
    "电力": "428",
}


# Detail page templates keyed by coarse report kind.
