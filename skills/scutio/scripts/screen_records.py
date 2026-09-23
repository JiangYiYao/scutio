"""Deterministic filtering of an explicitly described input sample, without opportunity scores."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scutio_data.paths import atomic_write_text
from scutio_data.screening import screen_records


def screen(payload):
    if not isinstance(payload, dict):
        raise ValueError("input must be an object")
    try:
        return screen_records(**payload)
    except TypeError as exc:
        raise ValueError(
            "input requires universe, as_of, records and filters; check field names"
        ) from exc


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
