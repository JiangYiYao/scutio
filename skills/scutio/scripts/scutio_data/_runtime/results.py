"""统一成功、失败和列表结果契约。"""


def result_ok(source=None, **payload):
    """成功 dict 信封：``{ok: True, error: None, source, ...}``。

    ``payload`` 中的 ``ok`` / ``error`` 会被忽略，以免破坏契约。
    """
    payload.pop("ok", None)
    payload.pop("error", None)
    out = {"ok": True, "error": None, "source": source}
    out.update(payload)
    return out


def result_err(error, source=None, **payload):
    """失败 dict 信封：``{ok: False, error: str, source, ...}``。

    ``payload`` 中的 ``ok`` / ``error`` 以本函数为准。
    """
    payload.pop("ok", None)
    payload.pop("error", None)
    out = {
        "ok": False,
        "error": str(error) if error is not None else "unknown_error",
        "source": source,
    }
    out.update(payload)
    return out


def result_list(items=None, source=None, **extra):
    """列表成功信封：``{ok: True, error: None, source, items, ...}``。

    合法空列表：``items=[]`` 且 ``ok=True``（如非交易日池）。
    """
    extra.pop("ok", None)
    extra.pop("error", None)
    extra.pop("items", None)
    return result_ok(source=source, items=list(items or []), **extra)


def result_list_err(error, source=None, items=None, **extra):
    """列表失败信封：``{ok: False, error, source, items=[]|...}``。"""
    extra.pop("ok", None)
    extra.pop("error", None)
    extra.pop("items", None)
    return result_err(error, source=source, items=list(items or []), **extra)


def envelope_items(envelope):
    """从统一 dict 信封取列表（``items``）；非 dict 返回空 list。"""
    if isinstance(envelope, dict):
        return list(envelope.get("items") or [])
    return []
