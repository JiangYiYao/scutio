"""研报缓存目录、文件身份和日期解析。"""

import re
from pathlib import Path

from scutio_data._runtime.symbols import normalize_code
from scutio_data.paths import default_scutio_home, safe_cache_seg

# Agent 硬规则：本地文件不是「最新研报」的证明
REPORT_CACHE_NOTE = (
    "本地缓存≠最新研报列表；引用前须 stock_reports 核对 publishDate；"
    "禁止只扫 cache/documents/reports 下结论。"
)


def _report_code_from_record(record: dict) -> str:
    """研报记录 → 缓存子目录代码（主标的）。"""
    record = record or {}
    for key in (
        "stockCode",
        "stock_code",
        "secCode",
        "sec_code",
        "code",
        "ticker",
    ):
        raw = record.get(key)
        if raw is not None and str(raw).strip():
            pure = normalize_code(str(raw).strip())
            if pure.isdigit() and len(pure) <= 6:
                pure = pure.zfill(6) if len(pure) <= 6 else pure
            return safe_cache_seg(pure.upper() if pure.isalpha() else pure, "_misc")
    # 行业/策略研报无个股码
    return "_misc"


def default_reports_dir(code=None) -> Path:
    """卖方研报目录：``$SCUTIO_HOME/cache/documents/reports/{code}/``（按个股；无码用 ``_misc``）。"""
    base = Path(default_scutio_home()) / "cache" / "documents" / "reports"
    if code is None:
        return base
    return base / safe_cache_seg(code, "_misc")


def _parse_date_from_filename(name: str) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", name or "")
    return m.group(1) if m else ""
