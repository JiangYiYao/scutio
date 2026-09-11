"""User-scoped source preferences. Secrets never enter result envelopes."""

from __future__ import annotations

from scutio_data._runtime.config import KEY_NAME as KEY_NAME
from scutio_data._runtime.config import configure as configure
from scutio_data._runtime.config import (
    credential,
    enabled,
    home,
    mode,
    set_setting,
    settings,
)
from scutio_data._runtime.storage import private_write as private_write
from scutio_data._runtime.storage import read_json
from scutio_data.paths import state_dir


def status():
    from scutio_data._providers.akshare import maintenance as akshare_maintenance
    from scutio_data._providers.capabilities import DIRECT_ADAPTERS

    key, origin = credential()
    import hashlib
    import time

    health = (
        read_json(
            state_dir()
            / "hithink"
            / hashlib.sha256((key or "").encode()).hexdigest()[:24]
            / "health.json"
        )
        if key
        else {}
    )
    return {
        "akshare": akshare_maintenance.status(),
        "mode": mode(),
        "hithink_configured": bool(key),
        "credential_source": origin,
        "hithink_enabled": enabled(),
        "permission_status": "not_checked",
        "verified_capabilities": health.get("verified_capabilities", {}),
        "cooldown_until": health.get("cooldown")
        if health.get("cooldown", 0) > time.time()
        else None,
        "capability_cooldowns": {
            path: until
            for path, until in health.items()
            if path.startswith("/api/") and until > time.time()
        },
        "free_sources": ["tencent", "sina", "eastmoney", "cninfo"],
        "free_adapter": "akshare + direct adapters",
        "source_policy": "financial_api_akshare_with_direct_adapters",
        "direct_adapters": list(DIRECT_ADAPTERS),
    }


def reuse_context() -> dict:
    """用于采集结果复用的配置身份，不返回原始凭据。"""
    import hashlib

    key, _ = credential()
    active = mode() == "auto" and bool(key)
    return {
        "mode": mode(),
        "hithink_enabled": active,
        "financial_detail": "full",
        "credential_namespace": hashlib.sha256(key.encode()).hexdigest()[:24] if active else None,
    }


def hint(market):
    if market != "a" or mode() == "public" or credential()[0] or settings().get("hint_seen"):
        return None
    set_setting("hint_seen", True)
    return (
        "当前使用免费数据源；如已有同花顺 Financial API Key，我可以帮你保存到本机，"
        "后续优先使用其 A 股数据，失败时仍自动回退。"
    )


__all__ = [
    "status",
    "reuse_context",
    "hint",
    "configure",
    "mode",
    "enabled",
    "credential",
    "home",
    "settings",
    "set_setting",
    "KEY_NAME",
    "read_json",
    "private_write",
]
