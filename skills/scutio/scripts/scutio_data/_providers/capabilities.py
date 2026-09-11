"""保留的直接适配能力与标准源覆盖限制；替代条件见研究数据范围文档。"""

from typing import TypedDict


class DirectAdapter(TypedDict):
    id: str
    status: str
    providers: list[str]
    reason: str


DIRECT_ADAPTERS: tuple[DirectAdapter, ...] = (
    {
        "id": "quotes",
        "status": "retained",
        "reason": "报价字段与时点尚未同构：AKShare 腾讯全市场缺报价时间/盘口，东财盘口连接失败，美股全市场超时",
        "providers": ["tencent", "sina", "eastmoney"],
    },
    {
        "id": "us_macro",
        "status": "retained",
        "reason": "核心 CPI/PPI 年率、政策上下限口径缺口；九个金十历史序列过期时保留最新值限定查询",
        "providers": ["eastmoney"],
    },
    {
        "id": "fx_timestamp",
        "status": "retained",
        "reason": "AKShare 外汇快照缺源时间且双线路连接失败；保留带时点 USDCNY 单符号报价",
        "providers": ["sina"],
    },
    {
        "id": "forecast_ranges",
        "status": "retained",
        "reason": "AKShare 预告删除金额/增幅上下限；文本无法完整可靠恢复",
        "providers": ["eastmoney"],
    },
    {
        "id": "concept_membership",
        "status": "retained",
        "reason": "反查个股需构建全板块成员图；目录和成员候选代理失败，直连仍断开，未完成图构建验收",
        "providers": ["eastmoney"],
    },
    {
        "id": "non_stock_research",
        "status": "retained",
        "reason": "AKShare 个股研报不覆盖行业/策略/宏观/晨会与券商维度列表",
        "providers": ["eastmoney"],
    },
    {
        "id": "sec_filings",
        "status": "retained",
        "reason": "AKShare/Financial API 无 SEC accession、forms 与 Archives 原文列表同构入口",
        "providers": ["sec"],
    },
)
