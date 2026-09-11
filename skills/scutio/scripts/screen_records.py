"""Deterministic filtering of an explicitly described input sample, without opportunity scores."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scutio_data._runtime.parsing import formatted_number as _number
from scutio_data._runtime.results import result_ok
from scutio_data.paths import atomic_write_text
from scutio_data.research.local import local_stock_screen


def screen(payload):
    if not isinstance(payload, dict):
        raise ValueError("input must be an object")
    for field in ("universe", "as_of"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError(f"{field} must describe the input sample and its time")
    records = payload.get("records")
    filters = payload.get("filters")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("records must be an array of objects")
    if not isinstance(filters, dict) or not filters:
        raise ValueError("explicit filters are required")
    for field, rule in filters.items():
        if not isinstance(field, str) or not field:
            raise ValueError("filter field must be a nonempty string")
        if isinstance(rule, dict):
            if not rule or set(rule) - {"min", "max", "eq", "contains"}:
                raise ValueError("unsupported filter operator")
            for op in ("min", "max"):
                if op in rule and (
                    not isinstance(rule[op], (int, float)) or _number(rule[op]) is None
                ):
                    raise ValueError("min/max must be finite numeric thresholds in input units")
            if "min" in rule and "max" in rule and rule["min"] > rule["max"]:
                raise ValueError("min must not exceed max")
            if "eq" in rule and rule["eq"] is None:
                raise ValueError("missing values cannot be a positive match")
            if "contains" in rule and not isinstance(rule["contains"], str):
                raise ValueError("contains must be a string")
        elif rule is None or isinstance(rule, (list, tuple)):
            raise ValueError("filter must be a scalar or supported operators")
    limit = payload.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError("limit must be a positive integer or null")
    sort_by = payload.get("sort_by")
    if sort_by is not None and (not isinstance(sort_by, str) or not sort_by):
        raise ValueError("sort_by must be an input numeric field")
    if not isinstance(payload.get("descending", True), bool):
        raise ValueError("descending must be boolean")

    # Use neutral temporary field names to bypass legacy aliases in the helper.
    names = {field: f"field_{i}" for i, field in enumerate(filters)}
    rules = {names[field]: rule for field, rule in filters.items()}
    missing, matched, excluded = [], [], 0
    for index, row in enumerate(records):
        absent = [
            field
            for field, rule in filters.items()
            if row.get(field) is None
            or (
                isinstance(rule, dict)
                and ("min" in rule or "max" in rule)
                and _number(row.get(field)) is None
            )
        ]
        if absent:
            missing.append({"row_index": index, "fields": absent})
            continue
        projected = {names[field]: row[field] for field in filters}
        if local_stock_screen([projected], filters=rules, limit=None):
            matched.append(dict(row))
        else:
            excluded += 1
    if sort_by:
        present = [row for row in matched if _number(row.get(sort_by)) is not None]
        absent_sort = [row for row in matched if _number(row.get(sort_by)) is None]
        present.sort(key=lambda row: _number(row[sort_by]), reverse=payload.get("descending", True))
        matched = present + absent_sort
    items = matched[:limit] if limit is not None else matched
    return result_ok(
        source="local_stock_screen",
        universe=payload["universe"],
        as_of=payload["as_of"],
        filters=filters,
        sort_by=sort_by,
        descending=payload.get("descending", True),
        input_count=len(records),
        matched_count=len(matched),
        returned_count=len(items),
        excluded_count=excluded,
        missing_count=len(missing),
        missing_rows=missing,
        items=items,
        coverage="provided_records_only",
        note="Counts describe input rows; ordering is not an investment ranking.",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Filter provided records within an explicit sample"
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = screen(json.loads(args.input.read_text(encoding="utf-8")))
        text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            atomic_write_text(args.output, text + "\n")
        print(text)
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
