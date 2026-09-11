"""东财个股概念成员查询。"""

from __future__ import annotations

from scutio_data._providers import eastmoney
from scutio_data._providers.eastmoney import em_secid
from scutio_data._runtime.results import result_err, result_ok
from scutio_data._runtime.symbols import require_a_share


def concept_blocks(code):
    """个股所属板块概念。返回 result_ok / result_err。"""
    try:
        _, prefix, code = require_a_share(code, "concept_blocks")
        params = {
            "fltt": "2",
            "invt": "2",
            "secid": em_secid(prefix + code),
            "spt": "3",
            "pi": "0",
            "pz": "200",
            "po": "1",
            "fields": "f12,f14,f3,f128",
        }
        data = eastmoney.em_get(
            "https://push2.eastmoney.com/api/qt/slist/get",
            params=params,
            headers={"Referer": "https://quote.eastmoney.com/"},
            timeout=15,
        ).json()
        items = (data.get("data") or {}).get("diff") or []
        items = items.values() if isinstance(items, dict) else items
        boards = [
            {
                "name": x.get("f14", ""),
                "code": x.get("f12", ""),
                "change_pct": x.get("f3", ""),
                "lead_stock": x.get("f128", ""),
            }
            for x in items
        ]
        return result_ok(
            source="concept_blocks",
            total=len(boards),
            boards=boards,
            concept_tags=[x["name"] for x in boards],
        )
    except Exception as exc:
        return result_err(
            exc,
            source="concept_blocks",
            error_code=(
                "unsupported_market" if str(exc).startswith("unsupported_market:") else None
            ),
            total=0,
            boards=[],
            concept_tags=[],
        )
