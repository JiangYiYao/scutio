"""进程环境标志、统一时区和公开 HTTP 标识。"""

import os
from zoneinfo import ZoneInfo


def _env_flag(name: str, default: str = "1") -> bool:
    """读取布尔环境变量（0/false/no/off 为 False）。"""
    raw = os.environ.get(name, default)
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


CN_TZ = ZoneInfo("Asia/Shanghai")
