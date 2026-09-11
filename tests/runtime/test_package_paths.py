"""Package path discovery and result envelope contracts."""

from __future__ import annotations

import sys


def test_find_scripts_dir_from_package_layout():
    from scutio_data.paths import ensure_on_syspath, find_scripts_dir

    found = find_scripts_dir()
    assert found is not None
    assert (found / "scutio_data").is_dir()
    path = ensure_on_syspath()
    assert path is not None
    assert path == str(found)
    assert sys.path[0] == path or path in sys.path


def test_default_python_path_is_platform_aware(monkeypatch):
    from scutio_data import paths

    monkeypatch.delenv("SCUTIO_PYTHON", raising=False)
    monkeypatch.delenv("SCUTIO_VENV", raising=False)
    monkeypatch.setenv("SCUTIO_HOME", "/tmp/scutio-home-test")
    p = paths.default_python_path()
    if sys.platform == "win32":
        assert p.as_posix().endswith("Scripts/python.exe") or str(p).endswith("Scripts\\python.exe")
    else:
        assert str(p).endswith(".venv/bin/python") or p.name == "python"


def test_path_helpers_importable_from_package_root():
    from scutio_data import default_python_path, default_scutio_home, ensure_on_syspath
    from scutio_data.paths import ensure_toolkit_for_skill_script

    assert callable(default_scutio_home)
    assert callable(default_python_path)
    assert callable(ensure_on_syspath)
    assert callable(ensure_toolkit_for_skill_script)


def test_ensure_toolkit_for_skill_scripts_from_collectors():
    from pathlib import Path

    from scutio_data.paths import ensure_toolkit_for_skill_script

    scripts = Path(__file__).resolve().parents[2] / "skills" / "scutio" / "scripts" / "collectors"
    for name in ("collect_research_base.py",):
        path = ensure_toolkit_for_skill_script(scripts / name)
        assert path is not None
        assert Path(path).name == "scripts"
        assert (Path(path) / "scutio_data").is_dir()


def test_collectors_have_one_public_entry():
    from pathlib import Path

    directory = Path(__file__).resolve().parents[2] / "skills/scutio/scripts/collectors"
    assert {path.name for path in directory.glob("*.py") if not path.name.startswith("_")} == {
        "collect_research_base.py"
    }
    assert not (directory / "_snapshot").exists()


def test_result_ok_and_result_err_envelope():
    from scutio_data._runtime.results import (
        envelope_items,
        result_err,
        result_list,
        result_list_err,
        result_ok,
    )

    ok = result_ok(source="unit", price=10.0, code="600519")
    assert ok == {
        "ok": True,
        "error": None,
        "source": "unit",
        "price": 10.0,
        "code": "600519",
    }

    err = result_err("quote_missing", source="unit", code="600519")
    assert err["ok"] is False
    assert err["error"] == "quote_missing"
    assert err["source"] == "unit"
    assert err["code"] == "600519"

    dr = result_list([{"a": 1}], source="pool")
    assert dr["ok"] is True
    assert dr["items"] == [{"a": 1}]
    assert envelope_items(dr) == [{"a": 1}]
    failed = result_list_err("boom", source="pool")
    assert failed["ok"] is False
    assert failed["items"] == []


def test_valuation_snapshot_returns_err_envelope_on_quote_failure(monkeypatch):
    from scutio_data import valuation

    def boom(_codes):
        raise RuntimeError("network down")

    monkeypatch.setattr(valuation, "security_quote", boom)
    out = valuation.valuation_snapshot("600519")
    assert out["ok"] is False
    assert "network down" in out["error"]
    assert out["source"] == "security_quote"
    assert out["code"] == "600519"


def test_valuation_snapshot_returns_ok_envelope(monkeypatch):
    from scutio_data import valuation

    monkeypatch.setattr(
        valuation,
        "security_quote",
        lambda codes: {
            "ok": True,
            "partial": False,
            "error": None,
            "errors": {},
            "sources_used": ["tencent"],
            "missing": [],
            "quotes": {
                "sh600519": {
                    "name": "贵州茅台",
                    "price": 1600.0,
                    "mcap_yi": 20000.0,
                    "pe_ttm": 25.0,
                    "pb": 8.0,
                    "code": "600519",
                    "symbol": "sh600519",
                    "source": "tencent",
                },
                "600519": {
                    "name": "贵州茅台",
                    "price": 1600.0,
                    "mcap_yi": 20000.0,
                    "pe_ttm": 25.0,
                    "pb": 8.0,
                    "code": "600519",
                    "symbol": "sh600519",
                    "source": "tencent",
                },
            },
        },
    )

    out = valuation.valuation_snapshot("600519")
    assert out["ok"] is True
    assert out["error"] is None
    assert out["source"] == "tencent"
    assert out["name"] == "贵州茅台"
    assert out["price"] == 1600.0
    assert out["pe_ttm"] == 25.0
    assert out["pb"] == 8.0
    assert "eps_cur" not in out
    assert "pe_fwd" not in out
