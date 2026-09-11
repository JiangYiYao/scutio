"""User-scoped source preferences. Secrets never enter result envelopes."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from scutio_data._runtime.storage import file_lock, private_write, read_json
from scutio_data.paths import config_dir, default_scutio_home

KEY_NAME = "HITHINK_FINANCE_API_KEY"
_settings_lock = threading.Lock()


def home():
    if "SCUTIO_DATA_HOME" in os.environ:
        raise ValueError(
            "use local_storage.py migrate --legacy-data-home before replacing SCUTIO_DATA_HOME with SCUTIO_CONFIG_DIR"
        )
    target = config_dir()
    if "SCUTIO_CONFIG_DIR" not in os.environ:
        legacy = default_scutio_home() / "data_sources"
        if any((legacy / name).is_file() for name in ("credentials.env", "settings.json")):
            raise ValueError("legacy configuration found; run local_storage.py migrate --apply")
    return target


def settings():
    return read_json(home() / "settings.json")


def set_setting(name, value):
    target = home() / "settings.json"
    with _settings_lock, file_lock(target.with_suffix(".lock")):
        data = read_json(target)
        data[name] = value
        private_write(target, json.dumps(data))


def mode():
    value = os.environ.get("SCUTIO_DATA_MODE", settings().get("mode", "auto"))
    if value not in ("auto", "public"):
        raise ValueError("SCUTIO_DATA_MODE must be auto or public")
    return value


def credential():
    value = os.environ.get(KEY_NAME, "").strip()
    if value:
        return value, "environment"
    paths = [home() / "credentials.env"]
    # A dedicated config directory also provides hermetic testing/configuration.
    if "SCUTIO_CONFIG_DIR" not in os.environ:
        if sys.platform == "darwin":
            root = Path.home() / "Library/Application Support"
        elif sys.platform == "win32":
            root = Path(os.environ.get("APPDATA", Path.home()))
        else:
            root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        paths.append(root / "hithink-finance/credentials.env")
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            name, separator, value = line.strip().partition("=")
            if separator and name == KEY_NAME and value.strip():
                return value.strip().strip("\"'"), "credentials_file"
    return None, None


def enabled():
    return mode() == "auto" and bool(credential()[0])


def configure(key):
    key = key.strip()
    if not key or any(ch.isspace() for ch in key):
        raise ValueError("Key must be nonempty and contain no whitespace")
    private_write(home() / "credentials.env", KEY_NAME + "=" + key + "\n")
