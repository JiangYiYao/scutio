#!/usr/bin/env python3
"""Local research and decision journal for Scutio.

The program persists user-provided facts and append-only changes.  It performs
structural validation only; investment-value judgments remain with the agent
and user.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from scutio_data._runtime.symbols import canonical_symbol
from scutio_data.paths import default_scutio_home

SCHEMA_VERSION = "2.1"
STATES = {"DRAFT", "ACTIVE", "CLOSED"}
SOURCES = {
    "user_explicit",
    "conversation_context",
    "external_source",
    "assistant_inference",
}
SAFE_CODE = re.compile(r"^[A-Za-z0-9_-]+$")
IMMUTABLE_KEYS = {
    "schema_version",
    "decision_id",
    "created_at",
    "events",
    "capture",
    "record_kind",
    "subject",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_root() -> Path:
    return default_scutio_home() / "journal"


def load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(path))
    finally:
        if temp_path.exists():
            temp_path.unlink()


@contextmanager
def record_lock(target: Path):
    """Serialize one record transaction across processes; retain the lock inode."""
    directory = target.resolve().parent / ".locks"
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(target.resolve()).encode("utf-8")).hexdigest()
    fd = os.open(directory / (digest + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b") as stream:
        if os.name == "nt":
            import msvcrt

            # Windows can lock past EOF. Initializing this byte before acquiring
            # the lock races with another process that has already locked it.

            def acquire():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(stream, fcntl.LOCK_UN)

        deadline = time.monotonic() + 30
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("journal record is busy; retry the update") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            release()


def validate_id(code: Any) -> str:
    value = str(code or "").strip()
    if not value or not SAFE_CODE.fullmatch(value):
        raise ValueError("decision_id must contain only letters, digits, _ or -")
    return value


def validate_code(code: Any) -> str:
    value = str(code or "").strip()
    try:
        canonical_symbol(value)
    except ValueError as exc:
        raise ValueError("instrument.code: %s" % exc) from None
    return value


def validate_state(state: Any) -> str:
    value = str(state or "")
    if value not in STATES:
        raise ValueError(f"state must be one of {sorted(STATES)}")
    return value


def validate_decision(decision: Dict[str, Any]) -> None:
    if decision.get("schema_version") not in {"2.0", SCHEMA_VERSION}:
        raise ValueError(f"unsupported schema_version: {decision.get('schema_version')}")
    validate_id(decision.get("decision_id"))
    validate_state(decision.get("state"))
    instrument = decision.get("instrument")
    kind = decision.get("record_kind", "decision")
    if not isinstance(kind, str) or kind not in {"decision", "research"}:
        raise ValueError("record_kind must be decision|research")
    if kind == "research" and (
        not isinstance(decision.get("subject"), str) or not decision["subject"].strip()
    ):
        raise ValueError("subject is required for research records")
    if kind == "decision" or instrument is not None:
        if not isinstance(instrument, dict):
            raise ValueError("instrument must be an object")
        validate_code(instrument.get("code"))
        if not str(instrument.get("name") or "").strip():
            raise ValueError("instrument.name is required")
    capture = decision.get("capture")
    if not isinstance(capture, dict):
        raise ValueError("capture must be an object")
    if not str(capture.get("raw_user_note") or "").strip():
        raise ValueError("capture.raw_user_note is required")
    if not isinstance(decision.get("details"), dict):
        raise ValueError("details must be an object")
    if not isinstance(decision.get("source_reports"), list):
        raise ValueError("source_reports must be an array")
    if not isinstance(decision.get("events"), list):
        raise ValueError("events must be an array")


def merge_patch(target: Any, patch: Any) -> Any:
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def record_dir_from_input(root: Path, payload: Dict[str, Any], created_at: str) -> Path:
    instrument = payload.get("instrument")
    if payload.get("record_kind") == "research":
        code = "research"
    elif not isinstance(instrument, dict):
        raise ValueError("instrument must be an object")
    else:
        code = validate_code(instrument.get("code"))
    decision_id = payload.get("decision_id")
    if decision_id is None:
        day = str(payload.get("as_of") or created_at[:10]).replace("-", "")
        slug = code.lower().replace(".", "_")
        decision_id = f"{day}-{slug}-{uuid.uuid4().hex[:8]}"
    decision_id = validate_id(decision_id)
    return root / decision_id


def build_decision(payload: Dict[str, Any], created_at: str, decision_id: str) -> Dict[str, Any]:
    kind = payload.get("record_kind", "decision")
    instrument = copy.deepcopy(payload.get("instrument"))
    if instrument is not None:
        if not isinstance(instrument, dict):
            raise ValueError("instrument must be an object")
        instrument["code"] = validate_code(instrument.get("code"))
        instrument["name"] = str(instrument.get("name") or "").strip()
    note = str(payload.get("raw_user_note") or "").strip()
    if not note:
        raise ValueError("raw_user_note is required")
    decision = {
        "schema_version": SCHEMA_VERSION,
        "decision_id": decision_id,
        "created_at": created_at,
        "updated_at": created_at,
        "as_of": payload.get("as_of"),
        "state": "DRAFT",
        "instrument": instrument,
        "capture": {
            "raw_user_note": note,
            "source": "user_explicit",
            "confirmed": False,
        },
        "details": copy.deepcopy(payload.get("details") or {}),
        "source_reports": copy.deepcopy(payload.get("source_reports") or []),
        "events": [],
    }
    if kind != "decision":
        decision["record_kind"] = kind
        decision["subject"] = payload.get("subject")
    validate_decision(decision)
    decision["events"].append(event_for_create(decision))
    validate_decision(decision)
    return decision


def event_for_create(decision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "at": decision["created_at"],
        "type": "research_recorded"
        if decision.get("record_kind") == "research"
        else "decision_created",
        "source": "user_explicit",
        "confirmed": False,
        "note": decision["capture"]["raw_user_note"],
        "patch": {},
        "snapshot": {
            key: copy.deepcopy(decision[key])
            for key in (
                "capture",
                "details",
                "source_reports",
                "instrument",
                "subject",
                "record_kind",
                "as_of",
                "state",
                "created_at",
            )
            if key in decision
        },
    }


def validate_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(payload.get("type") or "").strip()
    note = str(payload.get("note") or "").strip()
    source = str(payload.get("source") or "assistant_inference")
    patch = payload.get("patch") or {}
    if not event_type:
        raise ValueError("event.type is required")
    if not note:
        raise ValueError("event.note is required")
    if source not in SOURCES:
        raise ValueError(f"event.source must be one of {sorted(SOURCES)}")
    if not isinstance(patch, dict):
        raise ValueError("event.patch must be an object")
    forbidden = IMMUTABLE_KEYS.intersection(patch)
    if forbidden:
        raise ValueError(f"event.patch cannot change immutable keys: {sorted(forbidden)}")
    instrument_patch = patch.get("instrument")
    if instrument_patch is not None:
        if not isinstance(instrument_patch, dict):
            raise ValueError("event.patch.instrument must be an object")
        if "code" in instrument_patch:
            raise ValueError("event.patch.instrument.code is immutable")
    if "state" in patch:
        validate_state(patch["state"])
    confirmed = payload.get("confirmed", False)
    if not isinstance(confirmed, bool):
        raise ValueError("event.confirmed must be a boolean")
    return {
        "at": now_iso(),
        "type": event_type,
        "source": source,
        "confirmed": confirmed,
        "note": note,
        "patch": copy.deepcopy(patch),
    }


def render_memo(decision: Dict[str, Any]) -> str:
    instrument = decision.get("instrument") or {}
    title = (
        f"{decision['subject']} · 研究记录"
        if decision.get("record_kind") == "research"
        else f"{instrument['name']}（{instrument['code']}）决策备忘录"
    )
    lines = [
        f"# {title}",
        "",
        f"- 状态：{decision['state']}",
        f"- 建立时间：{decision['created_at']}",
        f"- 最近更新：{decision['updated_at']}",
    ]
    if decision.get("as_of"):
        lines.append(f"- 记录时点：{decision['as_of']}")
    if instrument.get("market"):
        lines.append(f"- 市场：{instrument['market']}")
    lines.extend(
        [
            "",
            "## 用户原话",
            "",
            decision["capture"]["raw_user_note"],
            "",
            "## 当前记录",
            "",
            "```json",
            json.dumps(decision.get("details") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## 修订历史",
            "",
        ]
    )
    for event in decision.get("events") or []:
        marker = "已确认" if event.get("confirmed") else "待确认"
        lines.append(
            f"- {event.get('at', '')} · {event.get('type', '')} · "
            f"{event.get('source', '')} · {marker}：{event.get('note', '')}"
        )
    initial = (decision.get("events") or [{}])[0].get("snapshot")
    if initial:
        lines.extend(
            [
                "",
                "## 初始记录",
                "",
                "```json",
                json.dumps(initial, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def require_record_dir(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.parent != root.resolve():
        raise ValueError(f"record directory must be directly under {root.resolve()}")
    if not (resolved / "record.json").is_file():
        raise ValueError(f"record.json not found in {resolved}")
    return resolved


def write_memo(decision_dir: Path, decision: Dict[str, Any]) -> None:
    memo = render_memo(decision)
    target = decision_dir / "memo.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=".memo.md.", dir=str(target.parent))
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(memo)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(target))
    finally:
        if temp_path.exists():
            temp_path.unlink()


def command_create(args: argparse.Namespace) -> int:
    payload = load_json(args.input)
    created_at = now_iso()
    target = record_dir_from_input(args.root, payload, created_at)
    with record_lock(target):
        if target.exists():
            raise ValueError(f"record already exists: {target}")
        decision = build_decision(payload, created_at, target.name)
        write_json_atomic(target / "record.json", decision)
        write_memo(target, decision)
    print(str(target))
    return 0


def command_append(args: argparse.Namespace) -> int:
    target = require_record_dir(args.record_dir, args.root)
    with record_lock(target):
        decision = load_json(target / "record.json")
        event = validate_event(load_json(args.event))
        updated = merge_patch(decision, event["patch"])
        updated["updated_at"] = event["at"]
        updated.setdefault("events", []).append(event)
        validate_decision(updated)
        write_json_atomic(target / "record.json", updated)
        write_memo(target, updated)
    print(str(target))
    return 0


def command_show(args: argparse.Namespace) -> int:
    target = require_record_dir(args.record_dir, args.root)
    print(
        json.dumps(load_json(target / "record.json"), ensure_ascii=False, indent=2, sort_keys=True)
    )
    return 0


def command_list(args: argparse.Namespace) -> int:
    records = []
    base = args.root
    for path in sorted(base.glob("*/record.json")) if base.exists() else []:
        try:
            decision = load_json(path)
            records.append(
                {
                    "decision_id": decision.get("decision_id"),
                    "state": decision.get("state"),
                    "instrument": decision.get("instrument"),
                    "updated_at": decision.get("updated_at"),
                    "path": str(path.parent),
                }
            )
            if decision.get("record_kind") == "research":
                records[-1].update(record_kind="research", subject=decision.get("subject"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            records.append({"path": str(path.parent), "error": str(exc)})
    print(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_render(args: argparse.Namespace) -> int:
    target = require_record_dir(args.record_dir, args.root)
    with record_lock(target):
        decision = load_json(target / "record.json")
        write_memo(target, decision)
    print(str(target / "memo.md"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scutio local research and decision journal")
    parser.add_argument("--root", type=Path, default=default_root())
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="save a DRAFT research record or decision")
    create.add_argument("--input", type=Path, required=True)
    create.set_defaults(func=command_create)

    append = subparsers.add_parser("append", help="append an event and update the projection")
    append.add_argument("--record-dir", type=Path, required=True)
    append.add_argument("--event", type=Path, required=True)
    append.set_defaults(func=command_append)

    show = subparsers.add_parser("show", help="show the current record projection")
    show.add_argument("--record-dir", type=Path, required=True)
    show.set_defaults(func=command_show)

    listing = subparsers.add_parser("list", help="list local research and decision records")
    listing.set_defaults(func=command_list)

    render = subparsers.add_parser("render", help="regenerate memo.md")
    render.add_argument("--record-dir", type=Path, required=True)
    render.set_defaults(func=command_render)
    return parser


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.root = args.root.expanduser().resolve()
    try:
        if any((args.root / "decisions").glob("*/*/decision.json")):
            quoted_root = (
                "'" + str(args.root).replace("'", "''") + "'"
                if os.name == "nt"
                else shlex.quote(str(args.root))
            )
            raise ValueError(
                "legacy journal found; run local_storage.py migrate "
                f"--journal-root {quoted_root} --apply"
            )
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
