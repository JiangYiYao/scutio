"""SEC 证券映射、披露列表与原文地址。"""

from __future__ import annotations

import os
from datetime import date
from typing import Dict, List, Tuple

from scutio_data._documents.hints import FALLBACK_HINT_US
from scutio_data._runtime import http
from scutio_data._runtime.execution import CacheSpec, execute
from scutio_data._runtime.results import result_list, result_list_err
from scutio_data._runtime.symbols import normalize_code
from scutio_data._runtime.timeouts import source

# SEC fair-access：须可识别的 UA；可用 SCUTIO_SEC_UA 设真实联系邮箱
_SEC_UA = (
    os.environ.get("SCUTIO_SEC_UA")
    or "ScutioToolkit (set SCUTIO_SEC_UA=YourApp contact@example.com)"
).strip()


_SEC_HEADERS = {
    "User-Agent": _SEC_UA,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,text/html,*/*",
}


_US_FORMS = {
    "annual": ("10-K", "10-K/A", "20-F", "20-F/A"),
    "semi": ("10-Q", "10-Q/A"),
    "q1": ("10-Q", "10-Q/A"),
    "q3": ("10-Q", "10-Q/A"),
    "all": ("10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A"),
}


def _load_sec_tickers(force: bool = False) -> Dict[str, str]:
    def load():
        response = http.get(
            "https://www.sec.gov/files/company_tickers.json", headers=_SEC_HEADERS, timeout=30
        )
        response.raise_for_status()
        data = response.json()
        out = {}
        for row in data.values() if isinstance(data, dict) else data:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            cik = str(row.get("cik_str") or row.get("cik") or "").strip()
            if ticker and cik.isdigit():
                out[ticker] = cik.zfill(10)
        return out

    return execute(
        "sec",
        "/files/company_tickers.json",
        load,
        cache=None
        if force
        else CacheSpec(
            ttl=86400,
            version=1,
            validator=lambda value: (
                isinstance(value, dict)
                and all(
                    isinstance(k, str) and isinstance(v, str) and len(v) == 10 and v.isdigit()
                    for k, v in value.items()
                )
            ),
        ),
    )


def resolve_us_cik(code: str) -> str:
    """``usAAPL`` / ``AAPL.US`` → 10 位 CIK。"""
    pure = normalize_code(code).upper()
    mapping = _load_sec_tickers()
    cik = mapping.get(pure)
    if not cik:
        raise ValueError("SEC CIK not found for ticker %r" % pure)
    return cik


@source("history")
def load_submissions(code: str, refresh: bool = False) -> Tuple[str, str, dict]:
    """SEC submissions shared by exact CIK through the common cache and quota."""
    pure = normalize_code(code).upper()
    cik = resolve_us_cik(code)
    path = "/submissions/CIK%s.json" % cik

    def load():
        response = http.get("https://data.sec.gov" + path, headers=_SEC_HEADERS, timeout=40)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("SEC submissions must be an object")
        return data

    data = execute(
        "sec",
        path,
        load,
        parameters={"cik": cik},
        cache=None
        if refresh
        else CacheSpec(
            ttl=300,
            version=1,
            identity={"cik": cik},
            validator=lambda value: (
                isinstance(value, dict)
                and ("cik" not in value or str(value["cik"]).zfill(10) == cik)
            ),
        ),
    )
    return pure, cik, data


def _us_forms_for_kind(kind: str) -> Tuple[str, ...]:
    key = str(kind or "annual").strip().lower()
    return _US_FORMS.get(key) or _US_FORMS["annual"]


def document_url(cik10: str, accession: str, primary: str) -> str:
    cik_int = str(int(cik10))
    acc = accession.replace("-", "")
    return "https://www.sec.gov/Archives/edgar/data/%s/%s/%s" % (
        cik_int,
        acc,
        primary.lstrip("/"),
    )


def _quarter_kind(report_date: str, annual_dates: List[date]) -> str:
    """从前一年度实际截止日推断财季，允许 52/53 周财年；无法判断则不标季度。"""
    try:
        reported = date.fromisoformat(report_date)
    except (TypeError, ValueError):
        return "quarter"
    previous = [day for day in annual_dates if day < reported]
    if not previous:
        return "quarter"
    elapsed = (reported - max(previous)).days
    for quarter, label in ((1, "q1"), (2, "semi"), (3, "q3")):
        if abs(elapsed - quarter * 365.25 / 4) <= 21:
            return label
    return "quarter"


def periodic_reports_us(
    code: str, kind: str = "annual", page_size: int = 20, page_num: int = 1
) -> dict:
    """美股定期报告列表（SEC submissions）。"""
    try:
        forms_ok = set(_us_forms_for_kind(kind))
        pure, cik, data = load_submissions(code)
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        primaries = recent.get("primaryDocument") or []
        dates = recent.get("filingDate") or []
        report_dates = recent.get("reportDate") or []
        annual_dates = []
        for form, reported in zip(forms, report_dates):
            if form.startswith(("10-K", "20-F")):
                try:
                    annual_dates.append(date.fromisoformat(reported))
                except (TypeError, ValueError):
                    pass
        unclassified_quarters = 0
        items: List[dict] = []
        for i, form in enumerate(forms):
            if form not in forms_ok:
                continue
            if i >= len(accessions) or i >= len(primaries) or i >= len(dates):
                continue
            acc = accessions[i]
            prim = primaries[i]
            if not prim:
                continue
            low = prim.lower()
            file_format = "pdf" if low.endswith(".pdf") else "html"
            url = document_url(cik, acc, prim)
            reported = report_dates[i] if i < len(report_dates) else ""
            quarterly = form.startswith("10-Q")
            kind_label = _quarter_kind(reported, annual_dates) if quarterly else "annual"
            if kind_label == "quarter":
                unclassified_quarters += 1
            if str(kind).lower() in ("semi", "q1", "q3") and kind_label != str(kind).lower():
                continue
            items.append(
                {
                    "title": "%s %s" % (form, dates[i]),
                    "type": form,
                    "date": dates[i],
                    "report_date": reported,
                    "announcement_id": acc,
                    "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%s"
                    % cik,
                    "pdf_url": url if file_format == "pdf" else "",
                    "file_url": url,
                    "file_format": file_format,
                    "primary_document": prim,
                    "cik": cik,
                    "ticker": pure,
                    "kind": kind_label,
                    "kind_basis": "annual_report_date_interval" if quarterly else "sec_form",
                    "market": "us",
                    "source": "sec_edgar",
                }
            )
        # page_num is 1-based slice over filtered list
        ps = max(1, int(page_size or 20))
        pn = max(1, int(page_num or 1))
        start = (pn - 1) * ps
        page = items[start : start + ps]
        env = result_list(
            page,
            source="periodic_reports_us",
            market="us",
            kind=str(kind or "annual").lower(),
            ticker=pure,
            cik=cik,
            total=len(items),
            partial=bool(unclassified_quarters),
            unclassified_quarters=unclassified_quarters,
            fallback_hint=FALLBACK_HINT_US,
            note=(
                "仅 SEC submissions.recent（近端约 1000 条提交）内检索；"
                "更早 10-K 可能不在列表中，见 fallback_hint。"
                "10-Q 财季按前一年度报告截止日推断，需结合原文确认；"
                "不能判断的记录仅在 all 中保留为 quarter，不冒充具体季度。"
            ),
            coverage="sec_submissions_recent",
            excluded_current_forms=["6-K", "6-K/A"],
        )
        if not page:
            env["note"] = "SEC recent 内无匹配表单；" + FALLBACK_HINT_US
        return env
    except Exception as exc:
        return result_list_err(
            str(exc),
            source="periodic_reports_us",
            market="us",
            fallback_hint=FALLBACK_HINT_US,
            note=FALLBACK_HINT_US,
        )
