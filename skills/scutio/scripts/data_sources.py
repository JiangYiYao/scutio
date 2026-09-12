#!/usr/bin/env python3
"""Inspect/configure optional data sources without putting credentials in argv."""

import argparse
import getpass
import json
import sys

from scutio_data import data_sources as config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("status", "configure", "mode", "hint", "dismiss-hint", "check-akshare")
    )
    parser.add_argument("value", nargs="?")
    parser.add_argument("--stdin", action="store_true", help="Read Key from stdin, never argv")
    args = parser.parse_args()
    if args.action == "check-akshare":
        from scutio_data._providers.akshare.maintenance import check_now

        print(json.dumps(check_now(), ensure_ascii=True, indent=2))
        return
    if args.action == "configure":
        if args.value:
            parser.error("Key must be entered at the hidden prompt or via --stdin")
        config.configure(
            sys.stdin.readline() if args.stdin else getpass.getpass("Financial API Key: ")
        )
    elif args.action == "mode":
        if args.value not in ("auto", "public"):
            parser.error("mode requires auto or public")
        config.set_setting("mode", args.value)
    elif args.action == "dismiss-hint":
        config.set_setting("hint_seen", True)
    elif args.action == "hint":
        print(json.dumps({"hint": config.hint(args.value)}, ensure_ascii=True))
        return
    print(json.dumps(config.status(), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
