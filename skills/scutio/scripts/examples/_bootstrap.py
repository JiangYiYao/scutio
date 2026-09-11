"""示例公共：把 scutio/scripts 放进 sys.path。"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_importable() -> Path:
    """返回 scripts 目录；保证可 ``import scutio_data``。"""
    scripts = Path(__file__).resolve().parents[1]
    s = str(scripts)
    if s not in sys.path:
        sys.path.insert(0, s)
    try:
        from scutio_data.paths import ensure_on_syspath

        ensure_on_syspath()
    except Exception:
        pass
    return scripts
