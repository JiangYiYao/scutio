"""Offline import probe; never fetch data, install packages, or write configuration."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
from pathlib import Path

DEPENDENCIES = (
    "akshare",
    "requests",
    "pandas",
    "lxml",
    "xlrd",
    "exchange_calendars",
    "pypdf",
    "tzdata",
    "packaging",
)


def probe():
    scripts = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts))
    failures = []
    if sys.version_info < (3, 11):
        failures.append(
            {"module": "python", "type": "UnsupportedVersion", "error": "Python 3.11+ required"}
        )
    for module in (*DEPENDENCIES, "scutio_data.market"):
        try:
            importlib.import_module(module)
        except Exception as exc:
            failures.append({"module": module, "type": type(exc).__name__, "error": str(exc)})
    versions = {}
    for module in DEPENDENCIES:
        try:
            versions[module] = importlib.metadata.version(module.replace("_", "-"))
        except importlib.metadata.PackageNotFoundError:
            versions[module] = None
    version_file = scripts.parent / "VERSION"
    return {
        "ok": not failures,
        "status": "ready" if not failures else "import_failed",
        "python": sys.executable,
        "version": sys.version.split()[0],
        "scutio_version": version_file.read_text(encoding="utf-8").strip()
        if version_file.is_file()
        else "unknown",
        "dependencies": versions,
        "scripts": str(scripts),
        "failures": failures,
        "network_checked": False,
    }


if __name__ == "__main__":
    result = probe()
    print(json.dumps(result, ensure_ascii=True))
    raise SystemExit(0 if result["ok"] else 2)
