"""Regression coverage for orchestration, import isolation and document ownership."""

import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from scutio_data import announcements, macro
from scutio_data._documents import filings, text
from scutio_data.macro import series

CN_NAMES = ("cpi_yoy", "pmi_mfg", "pmi_non_mfg", "forex_reserves", "rrr", "social_financing")
US_NAMES = ("us_cpi_yoy", "us_unemployment", "us_nfp", "us_ism_pmi", "us_fed_funds_upper")


def preloaded():
    return {
        "rates_snapshot": {"ok": True, "lpr": {}, "shibor": {}},
        "bond_yields_cn_us": {"ok": True, "items": [{"cn_10y": 2}]},
        "fx_usdcny": {"ok": True, "price": 7},
        "index_board": {"ok": True, "indices": []},
        "commodities_spot": {"ok": True, "gold": {"price": 100}},
        "series": {name: {"ok": True, "items": [{"name": name, "value": 1}]} for name in CN_NAMES},
    }


def test_failed_macro_series_are_requested_once_and_errors_survive(monkeypatch):
    calls = Counter()

    def failed(name):
        def fetch(limit):
            calls[name] += 1
            raise RuntimeError("source temporarily unavailable")

        return fetch

    for name in US_NAMES:
        monkeypatch.setitem(series.US_SERIES_SPECS, name, (failed(name), "fixture"))
    result = macro.macro_snapshot(preloaded=preloaded())
    assert result["ok"] and result["partial"]
    assert calls == Counter({name: 1 for name in US_NAMES})
    assert all(
        "source temporarily unavailable" in result["errors"]["series_" + name] for name in US_NAMES
    )


def test_macro_snapshot_propagates_partial_and_stale_coverage():
    supplied = preloaded()
    supplied["series"].update(
        {name: {"ok": True, "items": [{"name": name, "value": 1}]} for name in US_NAMES}
    )
    supplied["series"]["us_nfp"].update(stale=True, partial=True)
    supplied["series"]["cpi_yoy"]["partial"] = True
    result = macro.macro_snapshot(preloaded=supplied)
    assert result["ok"] and result["partial"]
    assert result["latest_series"]["us_nfp"]["value"] == 1
    assert set(result["errors"]) == {"series_us_nfp", "series_cpi_yoy"}


def test_hk_default_download_extracts_official_pdf_without_removed_content_adapter(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(filings, "download_filing_bytes", lambda *a, **k: b"%PDF-1.7 test original")
    monkeypatch.setattr(text, "extract_text_from_bytes", lambda *a, **k: "verified report text")
    result = announcements.download_announcement_pdf(
        {
            "market": "hk",
            "sec_code": "00700",
            "date": "2026-04-09",
            "title": "Annual report",
            "file_format": "pdf",
            "pdf_url": "https://static.cninfo.com.cn/finalpage/2026-04-09/1225088088.PDF",
        },
        target_dir=str(tmp_path),
    )
    assert result["ok"], result
    assert Path(result["path"]).read_bytes().startswith(b"%PDF")
    assert Path(result["text_path"]).read_text() == "verified report text"


def test_importing_result_contracts_does_not_load_source_transports():
    scripts = Path(__file__).resolve().parents[2] / "skills/scutio/scripts"
    code = "import sys; from scutio_data.core import result_ok, split_code; assert result_ok()['ok']; assert split_code('hk00700') == ('hk', '00700'); assert not any(name.startswith('scutio_data._providers.') for name in sys.modules)"
    run = subprocess.run(
        [sys.executable, "-B", "-c", code],
        env=dict(os.environ, PYTHONPATH=str(scripts)),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run.returncode == 0, run.stderr


def test_akshare_worker_entrypoint_imports_from_nested_location(tmp_path, local_worker_command):
    scripts = Path(__file__).resolve().parents[2] / "skills/scutio/scripts"
    worker = scripts / "scutio_data/_providers/akshare/_akshare_worker.py"
    # Unsupported function fails before importing AKShare or making a network request.
    run = subprocess.run(
        local_worker_command(
            [
                sys.executable,
                "-B",
                "-c",
                f"import runpy, sys; sys.modules['akshare'] = None; "
                f"runpy.run_path({str(worker)!r}, run_name='__main__')",
            ]
        ),
        input='{"function":"not_allowlisted","params":{}}',
        env=dict(os.environ, PYTHONPATH=""),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run.returncode != 0
    assert "unsupported adapter" in run.stderr
    assert "ModuleNotFoundError" not in run.stderr
