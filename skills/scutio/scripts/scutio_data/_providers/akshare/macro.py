"""AKShare macro tables mapped to research series, without source HTTP protocols."""

from __future__ import annotations

import calendar
import math
import re

from scutio_data._providers.akshare import client as akshare_source

CN_TABLES = {
    "cpi_yoy": {
        "function": "macro_china_cpi",
        "label": "月份",
        "date": None,
        "unit": "pct",
        "freq": "M",
        "fields": {
            "value": "全国-同比增长",
            "index": "全国-当月",
            "mom": "全国-环比增长",
            "ytd": "全国-累计",
        },
    },
    "ppi_yoy": {
        "function": "macro_china_ppi",
        "label": "月份",
        "date": None,
        "unit": "pct",
        "freq": "M",
        "fields": {"value": "当月同比增长", "index": "当月", "ytd": "累计"},
    },
    "pmi_mfg": {
        "function": "macro_china_pmi",
        "label": "月份",
        "date": None,
        "unit": "index",
        "freq": "M",
        "fields": {
            "value": "制造业-指数",
            "pmi_mfg_yoy": "制造业-同比增长",
            "pmi_non_mfg": "非制造业-指数",
            "pmi_non_mfg_yoy": "非制造业-同比增长",
        },
    },
    "m2_yoy": {
        "function": "macro_china_money_supply",
        "label": "月份",
        "date": None,
        "unit": "pct",
        "freq": "M",
        "fields": {
            "value": "货币和准货币(M2)-同比增长",
            "m2": "货币和准货币(M2)-数量(亿元)",
            "m1_yoy": "货币(M1)-同比增长",
            "m1": "货币(M1)-数量(亿元)",
            "m0_yoy": "流通中的现金(M0)-同比增长",
            "m0": "流通中的现金(M0)-数量(亿元)",
        },
    },
    "rmb_loan": {
        "function": "macro_china_new_financial_credit",
        "label": "月份",
        "date": None,
        "unit": "yi_cny",
        "freq": "M",
        "fields": {"value": "当月", "yoy": "当月-同比增长", "mom": "当月-环比增长", "ytd": "累计"},
    },
    "export_yoy": {
        "function": "macro_china_hgjck",
        "label": "月份",
        "date": None,
        "unit": "pct",
        "freq": "M",
        "fields": {
            "value": "当月出口额-同比增长",
            "export": "当月出口额-金额",
            "import_yoy": "当月进口额-同比增长",
            "import_val": "当月进口额-金额",
        },
    },
    "industrial_yoy": {
        "function": "macro_china_gyzjz",
        "label": "月份",
        "date": "发布时间",
        "unit": "pct",
        "freq": "M",
        "fields": {"value": "同比增长", "ytd": "累计增长"},
    },
    "retail_yoy": {
        "function": "macro_china_consumer_goods_retail",
        "label": "月份",
        "date": None,
        "unit": "pct",
        "freq": "M",
        "fields": {"value": "同比增长", "retail": "当月", "mom": "环比增长", "ytd": "累计"},
    },
    "fai_yoy": {
        "function": "macro_china_gdzctz",
        "label": "月份",
        "date": None,
        "unit": "pct",
        "freq": "M",
        "fields": {"value": "同比增长", "index": "当月", "mom": "环比增长", "ytd": "自年初累计"},
    },
    "gdp_yoy": {
        "function": "macro_china_gdp",
        "label": "季度",
        "date": None,
        "unit": "pct",
        "freq": "Q",
        "fields": {
            "value": "国内生产总值-同比增长",
            "gdp": "国内生产总值-绝对值",
            "primary_yoy": "第一产业-同比增长",
            "secondary_yoy": "第二产业-同比增长",
            "tertiary_yoy": "第三产业-同比增长",
        },
    },
    "pmi_non_mfg": {
        "function": "macro_china_pmi",
        "label": "月份",
        "date": None,
        "unit": "index",
        "freq": "M",
        "fields": {
            "value": "非制造业-指数",
            "pmi_non_mfg_yoy": "非制造业-同比增长",
            "pmi_mfg": "制造业-指数",
            "pmi_mfg_yoy": "制造业-同比增长",
        },
    },
    "import_yoy": {
        "function": "macro_china_hgjck",
        "label": "月份",
        "date": None,
        "unit": "pct",
        "freq": "M",
        "fields": {
            "value": "当月进口额-同比增长",
            "import_val": "当月进口额-金额",
            "export_yoy": "当月出口额-同比增长",
            "export": "当月出口额-金额",
        },
    },
    "rrr": {
        "function": "macro_china_reserve_requirement_ratio",
        "label": "公布时间",
        "date": "生效时间",
        "unit": "pct",
        "freq": "event",
        "fields": {
            "value": "大型金融机构-调整后",
            "rrr_large_before": "大型金融机构-调整前",
            "rrr_large_change": "大型金融机构-调整幅度",
            "rrr_small_before": "中小金融机构-调整前",
            "rrr_small_after": "中小金融机构-调整后",
            "rrr_small_change": "中小金融机构-调整幅度",
        },
    },
    "forex_reserves": {
        "function": "macro_china_fx_gold",
        "label": "月份",
        "date": None,
        "unit": "yi_usd",
        "freq": "M",
        "fields": {
            "value": "国家外汇储备-数值",
            "yoy": "国家外汇储备-同比",
            "mom": "国家外汇储备-环比",
            "gold_reserves": "黄金储备-数值",
            "gold_yoy": "黄金储备-同比",
        },
    },
    "gold_reserves": {
        "function": "macro_china_fx_gold",
        "label": "月份",
        "date": None,
        "unit": "yi_usd",
        "freq": "M",
        "fields": {
            "value": "黄金储备-数值",
            "yoy": "黄金储备-同比",
            "forex": "国家外汇储备-数值",
            "forex_yoy": "国家外汇储备-同比",
        },
    },
    "fdi": {
        "function": "macro_china_fdi",
        "label": "月份",
        "date": None,
        "unit": "k_usd",
        "freq": "M",
        "fields": {"value": "当月", "yoy": "当月-同比增长", "mom": "当月-环比增长", "ytd": "累计"},
    },
    "consumer_confidence": {
        "function": "macro_china_xfzxx",
        "label": "月份",
        "date": None,
        "unit": "index",
        "freq": "M",
        "fields": {
            "value": "消费者信心指数-指数值",
            "yoy": "消费者信心指数-同比增长",
            "satisfaction": "消费者满意指数-指数值",
            "expectation": "消费者预期指数-指数值",
        },
    },
    "boom_index": {
        "function": "macro_china_enterprise_boom_index",
        "label": "季度",
        "date": None,
        "unit": "index",
        "freq": "Q",
        "fields": {
            "value": "企业景气指数-指数",
            "yoy": "企业景气指数-同比",
            "enterprise_faith": "企业家信心指数-指数",
            "enterprise_faith_yoy": "企业家信心指数-同比",
        },
    },
    "tax_revenue": {
        "function": "macro_china_national_tax_receipts",
        "label": "季度",
        "date": None,
        "unit": "yi_cny",
        "freq": "Q",
        "fields": {"value": "税收收入合计", "yoy": "较上年同期", "mom": "季度环比"},
    },
}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def period_date(value):
    text = str(value or "")
    quarter = re.search(r"(\d{4})年?第?(?:1[-—])?([一二三四1234])季度", text)
    if quarter:
        q = "一二三四".find(quarter[2]) + 1 if quarter[2] in "一二三四" else int(quarter[2])
        year, month = int(quarter[1]), q * 3
        return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    match = re.match(r"(\d{4})[-/年]?(\d{2}|\d)(?:[-/月](\d{1,2}))?", text)
    if not match:
        raise ValueError("unrecognized macro period: " + text)
    year, month, day = int(match[1]), int(match[2]), int(match[3] or 1)
    from datetime import date

    return date(year, month, day).isoformat()


def cn_series(name, limit):
    spec = CN_TABLES[name]
    rows = akshare_source.fetch(spec["function"])
    items = []
    for row in rows:
        if spec["fields"]["value"] not in row:
            raise ValueError("AKShare macro value column missing")
        if number(row.get(spec["fields"]["value"])) is None:
            continue
        label = row.get(spec["label"])
        point = {key: number(row.get(column)) for key, column in spec["fields"].items()}
        point.update(
            name=name,
            period=label,
            date=period_date(row.get(spec["date"]) or label),
            date_basis="effective_date" if name == "rrr" else "observation_period",
            unit=spec["unit"],
            freq=spec["freq"],
            market="CN",
            source="akshare_" + spec["function"],
        )
        if name in ("gold_reserves", "forex_reserves"):
            point["gold_reserves_unit"] = "yi_usd"
            point["gold_measure"] = "monetary_value"
            # SAFE 2026-08: gold value 3500.80 (100 million USD), physical 7673 (10k oz).
            point["unit_reference"] = "https://www.safe.gov.cn/safe/2026/0206/27116.html"
        if name == "fdi":
            # MOFCOM Jan-May 2023: 84.35 billion USD; raw YTD is 84,350,000.
            point["units"] = {"value": "k_usd", "ytd": "k_usd", "yoy": "pct", "mom": "pct"}
            point["unit_reference"] = (
                "https://dcj.mofcom.gov.cn/article/xwfb/xwsjfzr/202306/20230603416692.shtml"
            )
        if name == "consumer_confidence":
            # AKShare 1.18.94 assigns satisfaction values to the expectation column.
            point.update(
                expectation=None,
                partial=True,
                missing_fields=["expectation"],
                warning="AKShare expectation column copies satisfaction; suppressed",
            )
        items.append(point)
    items.sort(key=lambda row: row["date"], reverse=True)
    return items[: max(1, int(limit))]


CN_DESCRIPTIONS = {
    "cpi_yoy": "CPI 全国同比 %",
    "ppi_yoy": "PPI 当月同比 %",
    "pmi_mfg": "官方制造业 PMI 指数（同行含非制造）",
    "pmi_non_mfg": "官方非制造业 PMI 指数",
    "m2_yoy": "M2 同比 %（同行含 M1/M0）",
    "rmb_loan": "新增人民币贷款 亿元",
    "social_financing": "社会融资规模增量 亿元（商务部；含贷款/债券/股票等分项）",
    "export_yoy": "出口金额同比 %（同行含进口）",
    "import_yoy": "进口金额同比 %（同行含出口）",
    "industrial_yoy": "规模以上工业增加值同比 %",
    "retail_yoy": "社会消费品零售总额同比 %",
    "fai_yoy": "城镇固定资产投资同比 %",
    "gdp_yoy": "GDP 同比 %",
    "rrr": "存款准备金率 %（大型机构调整后）",
    "forex_reserves": "外汇储备 亿美元",
    "gold_reserves": "黄金储备价值 亿美元（非实物重量）",
    "fdi": "实际使用外资（东财原值）",
    "consumer_confidence": "消费者信心指数",
    "boom_index": "企业景气指数",
    "tax_revenue": "税收收入 亿元",
}
