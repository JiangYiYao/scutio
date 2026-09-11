"""Shipped examples must follow the same public envelope contracts as the package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[2] / "skills" / "scutio" / "scripts" / "examples"


def _load(name: str, filename: str):
    if str(EXAMPLES) not in sys.path:
        sys.path.insert(0, str(EXAMPLES))
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_reports_local_example_unwraps_items(monkeypatch):
    module = _load("scutio_example_reports", "03_reports_local.py")
    rows = [{"title": "示例研报", "publishDate": "2026-08-05", "uid": "1"}]
    monkeypatch.setattr(
        module,
        "local_report_search",
        lambda *args, **kwargs: {
            "ok": True,
            "error": None,
            "source": "fixture",
            "items": rows,
        },
    )
    assert module.main(["03_reports_local.py", "测试"]) == 0


def test_quote_example_uses_one_public_batch_and_preserves_index_identity(monkeypatch):
    module = _load("scutio_example_quotes", "01_quote_and_bars.py")
    calls = []

    def quote(codes):
        calls.append(codes)
        return {"ok": True, "quotes": {}, "sources_used": ["fixture"]}

    monkeypatch.setattr(module, "security_quote", quote)
    monkeypatch.setattr(module, "security_bars", lambda *a, **k: {"ok": True, "bars": []})
    assert module.main() == 0
    assert calls == [["600519", "000001", "sh000001"]]


def test_dragon_tiger_example_passes_requested_date_and_failure(monkeypatch):
    module = _load("scutio_example_dragon_tiger", "04_dragon_tiger.py")
    calls = []

    def fetch(day):
        calls.append(day)
        return {"ok": False, "error": "fixture failure", "items": []}

    monkeypatch.setattr(module, "daily_dragon_tiger", fetch)
    assert module.main(["04_dragon_tiger.py", "2026-09-08"]) == 1
    assert calls == ["2026-09-08"]


def test_macro_example_reports_failed_series(monkeypatch):
    module = _load("scutio_example_macro", "06_macro_snapshot.py")
    monkeypatch.setattr(module, "list_macro_series", lambda: [])
    monkeypatch.setattr(module, "macro_snapshot", lambda: {"ok": True})
    monkeypatch.setattr(module, "cn_macro_series", lambda *a, **k: {"ok": False, "items": []})
    assert module.main() == 1


def test_hk_us_example_reports_failed_profile(monkeypatch):
    module = _load("scutio_example_international", "07_hk_us_quote_bars.py")
    monkeypatch.setattr(module, "security_quote", lambda *a, **k: {"ok": True, "quotes": {}})
    monkeypatch.setattr(module, "security_bars", lambda *a, **k: {"ok": True, "bars": []})
    monkeypatch.setattr(module, "stock_info", lambda *a, **k: {"ok": False, "error": "fixture"})
    assert module.main() == 1
