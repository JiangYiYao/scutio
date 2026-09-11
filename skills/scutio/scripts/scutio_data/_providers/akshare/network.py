"""AKShare 子进程的代理模式与隔离环境；不改变父进程配置。"""

from __future__ import annotations

import os


def network_mode() -> str:
    mode = os.environ.get("SCUTIO_AKSHARE_NETWORK", "auto").strip().lower()
    if mode not in ("auto", "environment", "direct"):
        raise ValueError("SCUTIO_AKSHARE_NETWORK must be auto, environment or direct")
    return mode


def worker_env(direct: bool = False) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if "KEY" not in key.upper() and "TOKEN" not in key.upper() and "SECRET" not in key.upper()
    }
    if direct:
        env = {key: value for key, value in env.items() if not key.lower().endswith("_proxy")}
        # NO_PROXY also disables macOS system proxy discovery in this worker.
        env.update(NO_PROXY="*", no_proxy="*")
    return env
