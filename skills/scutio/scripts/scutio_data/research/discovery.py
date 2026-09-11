"""个股研报发现与有限历史覆盖。"""

import re
from datetime import date

from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import require_a_share
from scutio_data._runtime.timeouts import operation


@operation("history")
def stock_reports(code, max_pages=5, begin="2000-01-01"):
    """个股研报列表，经 AKShare 获取并保留报告链接和盈利预测。"""
    try:
        _, prefix, code = require_a_share(code, "stock_reports")
        if type(max_pages) is not int or not 1 <= max_pages <= 50:
            raise ValueError("max_pages must be an integer between 1 and 50")
        begin = date.fromisoformat(begin).isoformat()
        from scutio_data._providers.akshare.client import fetch

        rows = fetch("stock_research_report_em", symbol=code)
        records = []
        for row in rows:
            if str(row.get("股票代码")) != code:
                raise ValueError("research report identity mismatch")
            published = str(row.get("日期") or "")[:10]
            if published < begin:
                continue
            url = str(row.get("报告PDF链接") or "")
            match = re.search(r"H3_([A-Za-z0-9]+)_1\.pdf", url)
            item = {
                "stockCode": code,
                "symbol": prefix + code,
                "stockName": row.get("股票简称"),
                "title": row.get("报告名称"),
                "publishDate": published,
                "orgSName": row.get("机构"),
                "emRatingName": row.get("东财评级"),
                "infoCode": match.group(1) if match else None,
                "pdf_url": url,
                "pdfUrl": url,
                "source": "akshare_eastmoney",
            }
            forecasts = sorted((key for key in row if re.match(r"\d{4}-盈利预测-收益", key)))
            item["forecast_years"] = [int(key[:4]) for key in forecasts[:3]]
            for offset, key in enumerate(forecasts[:3]):
                forecast_prefix = ("predictThisYear", "predictNextYear", "predictNextTwoYear")[
                    offset
                ]
                item[forecast_prefix + "Eps"] = row.get(key)
                item[forecast_prefix + "Pe"] = row.get(key.replace("收益", "市盈率"))
            records.append(item)
        records.sort(key=lambda row: row["publishDate"], reverse=True)
        available = len(records)
        limit = max_pages * 50
        records = records[:limit]
        return result_list(
            records or [],
            source="stock_reports",
            adapter="akshare",
            provider="eastmoney",
            code=code,
            symbol=prefix + code,
            partial=available > limit,
            coverage={
                "authors": False,
                "pdf_links": all(row.get("pdf_url") for row in records),
                "begin": begin,
                "available_count": available,
                "returned_count": len(records),
                "limit": limit,
                "truncated": available > limit,
                "limit_scope": "local_rows",
            },
        )
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="stock_reports",
            error_code=(
                str(exc).split(":", 1)[0]
                if str(exc).startswith(("unsupported_market:", "unsupported_asset:"))
                else None
            ),
        )
