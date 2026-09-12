"""Offline checks distinguish interpreter startup from Python import failures."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import runtime_probe

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills/scutio/scripts"


def test_probe_retains_import_error_type_and_does_not_stop_at_first(monkeypatch):
    seen = []

    def load(name):
        seen.append(name)
        if name == "pandas":
            raise ImportError("DLL load failed: access denied")
        if name == "pypdf":
            raise ModuleNotFoundError("No module named 'pypdf'")

    monkeypatch.setattr(runtime_probe.importlib, "import_module", load)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    result = runtime_probe.probe()
    assert not result["ok"] and result["status"] == "import_failed"
    assert [f["type"] for f in result["failures"]] == ["ImportError", "ModuleNotFoundError"]
    assert seen[-1] == "scutio_data.market"
    assert result["network_checked"] is False


def test_python_probe_runs_from_unrelated_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "runtime_probe.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["status"] == "ready"
    assert Path(report["scripts"]).resolve() == SCRIPTS.resolve()
    assert report["failures"] == []


def run_resolver(script, tmp_path, *, extra=(), configured_python=None):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    env = os.environ.copy()
    for key in ("SCUTIO_PYTHON", "SCUTIO_VENV", "PYTHONPATH", "SCUTIO_TOOLKIT_SCRIPTS"):
        env.pop(key, None)
    env["SCUTIO_HOME"] = str(tmp_path / "untouched user data")
    if configured_python is not None:
        env["SCUTIO_PYTHON"] = configured_python
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(script), *extra],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    report = json.loads(result.stdout)
    assert not (tmp_path / "untouched user data").exists()
    return result, report


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runtime resolver")
def test_resolver_respects_explicit_missing_interpreter(tmp_path):
    missing = str(tmp_path / "does not exist" / "python.exe")
    result, report = run_resolver(
        SCRIPTS / "resolve_runtime.ps1", tmp_path, configured_python=missing
    )
    assert result.returncode == 2
    assert report["status"] == "missing_interpreter"
    assert report["python"] == missing
    assert report["selection"] == "SCUTIO_PYTHON"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runtime resolver")
def test_resolver_probes_copied_skill_with_space_in_path(tmp_path):
    skill = tmp_path / "installed skills" / "scutio"
    shutil.copytree(SCRIPTS, skill / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    result, report = run_resolver(
        skill / "scripts/resolve_runtime.ps1",
        tmp_path,
        extra=("-Python", sys.executable),
        configured_python="invalid-override",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert report["status"] == "ready" and report["selection"] == "argument"
    assert Path(report["scripts"]).resolve() == (skill / "scripts").resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runtime resolver")
def test_existing_but_unlaunchable_file_is_startup_failure(tmp_path):
    executable = tmp_path / "invalid.exe"
    executable.write_bytes(b"not an executable")
    result, report = run_resolver(
        SCRIPTS / "resolve_runtime.ps1", tmp_path, extra=("-Python", str(executable))
    )
    assert result.returncode == 2
    assert report["status"] == "startup_failed"
    assert report["error"]
