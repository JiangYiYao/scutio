#!/usr/bin/env python3
"""Preview/apply local storage maintenance. No conversation memory is collected."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scutio_data._runtime.cache import clean_api_cache
from scutio_data._runtime.storage import file_lock
from scutio_data.paths import config_dir, default_scutio_home, state_dir


def migration_plan(*, legacy_data_home: Path | None = None, journal_root: Path | None = None):
    """Map existing files, refusing collisions before changing anything."""
    home = default_scutio_home().resolve()
    legacy = (legacy_data_home or home / "data_sources").expanduser().resolve()
    journal = (journal_root or home / "journal").expanduser().resolve()
    records = sorted((journal / "decisions").glob("*/*/decision.json"))
    reference_records = {
        path
        for root in {home / "journal", journal}
        for pattern in ("decisions/*/*/decision.json", "*/record.json")
        for path in root.glob(pattern)
    }
    record_texts = [p.read_text(encoding="utf-8") for p in sorted(reference_records)]
    moves = []
    retained = []

    def referenced(path):
        # Decode JSON escapes (including Windows backslashes); no content is logged.
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)

        spellings = {path.as_posix()}
        for root in (home, journal):
            try:
                spellings.add(path.relative_to(root).as_posix())
            except ValueError:
                pass
        return any(
            any(spelling in text.replace("\\", "/") for spelling in spellings)
            for raw in record_texts
            for text in strings(json.loads(raw))
        )

    def add(source, target):
        if source.exists() or source.is_symlink():
            moves.append((source, target))

    for name in ("credentials.env", "settings.json"):
        add(legacy / name, config_dir() / name)
    add(legacy / "settings.lock", config_dir() / "settings.lock")
    add(legacy / "akshare_health.json", state_dir() / "akshare" / "health.json")
    add(legacy / "akshare_health.lock", state_dir() / "akshare" / "health.lock")
    for name in ("filings", "reports"):
        source = home / "cache" / name
        if source.exists() and referenced(source):
            retained.append({"path": str(source), "reason": "referenced_by_journal"})
        else:
            add(source, home / "cache" / "documents" / name)
    for record in records:
        # Move the entire record directory, including user attachments, without rewriting history.
        if referenced(record.parent):
            raise ValueError(f"record contains references to its old directory: {record.parent}")
        add(record.parent, journal / record.parent.name)
    research = home / "cache" / "research"
    if research.exists():
        retained.append({"path": str(research), "reason": "user_exports_preserved"})
    targets = set()
    for source, target in moves:
        if any(p.is_symlink() for p in (source, *source.parents, target, *target.parents)):
            raise ValueError(f"migration does not follow symbolic links: {source}")
        if source.is_dir() and any(p.is_symlink() for p in source.rglob("*")):
            raise ValueError(f"migration does not follow symbolic links inside: {source}")
        if target.exists() or target in targets:
            raise ValueError(f"migration target already exists or is duplicated: {target}")
        if (
            source.is_dir()
            and (source / "decision.json").exists()
            and (source / "record.json").exists()
        ):
            raise ValueError(f"record has conflicting filenames: {source}")
        targets.add(target)
    return moves, retained


def migrate(*, apply=False, legacy_data_home=None, journal_root=None):
    journal = (journal_root or default_scutio_home() / "journal").expanduser().resolve()

    def run():
        moves, retained = migration_plan(legacy_data_home=legacy_data_home, journal_root=journal)
        completed = []
        renamed_records = set()
        if apply:
            try:
                for source, target in moves:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source.rename(target)
                    completed.append((source, target))
                    if target.parent == journal:
                        (target / "decision.json").rename(target / "record.json")
                        renamed_records.add(target)
            except OSError:
                for source, target in reversed(completed):
                    if target in renamed_records:
                        (target / "record.json").rename(target / "decision.json")
                    target.rename(source)
                raise
            # Remove empty containers only; unknown files and exports stay untouched.
            boundaries = {
                default_scutio_home().resolve(),
                journal,
                (legacy_data_home or default_scutio_home() / "data_sources")
                .expanduser()
                .resolve()
                .parent,
            }
            for source, _ in reversed(moves):
                parent = source.parent
                while parent not in boundaries and parent != parent.parent:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        return {
            "applied": apply,
            "moves": [{"from": str(s), "to": str(t)} for s, t in moves],
            "retained": retained,
        }

    if not apply:
        return run()
    # Old tasks must be stopped first; their locks used different locations.
    with (
        file_lock(state_dir() / "storage_migration.lock"),
        file_lock(journal / ".locks" / "migration.lock"),
    ):
        return run()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    migration = commands.add_parser("migrate", help="move known legacy files without overwriting")
    migration.add_argument("--legacy-data-home", type=Path)
    migration.add_argument(
        "--journal-root", type=Path, help="journal root used with journal.py --root"
    )
    migration.add_argument("--apply", action="store_true", help="apply the previewed moves")
    clean = commands.add_parser("clean-cache", help="remove expired/oldest API responses only")
    clean.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = (
            migrate(
                apply=args.apply,
                legacy_data_home=args.legacy_data_home,
                journal_root=args.journal_root,
            )
            if args.command == "migrate"
            else clean_api_cache(apply=args.apply)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
