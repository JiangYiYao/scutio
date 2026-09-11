"""Storage maintenance preserves credentials, journal history and source evidence."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import journal
import local_storage
import pytest
from scutio_data._runtime import cache, config
from scutio_data.paths import cache_dir, config_dir, default_scutio_home, state_dir


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


def legacy_record(record_id="20260911-example", *, code="_research", reports=None, root=None):
    record = journal.build_decision(
        {
            "record_kind": "research",
            "subject": "订单变化",
            "raw_user_note": "请记下这条线索。",
            "details": {"question": "能否转化为收入"},
            "source_reports": reports or [],
        },
        "2026-09-11T10:00:00+08:00",
        record_id,
    )
    directory = (root or default_scutio_home() / "journal") / "decisions" / code / record_id
    write(directory / "decision.json", record)
    write(directory / "memo.md", journal.render_memo(record))
    return directory


def test_migration_preserves_config_record_bytes_and_referenced_evidence(monkeypatch):
    home = default_scutio_home()
    old = home / "data_sources"
    secret = write(old / "credentials.env", config.KEY_NAME + "=test-token\n")
    secret.chmod(0o600)
    write(old / "settings.json", {"mode": "public"})
    write(old / "akshare_health.json", {"last_check_at": 100})
    write(old / "cache" / "example" / "health.json", {"cooldown": 9999999999})
    write(old / "cache" / "example" / "response.json", {"saved_at": 1})
    write(old / "akshare_snapshots" / "snapshot.json", {"items": []})
    evidence = write(home / "cache" / "filings" / "a" / "report.txt", "source evidence")
    report = write(home / "cache" / "reports" / "report.pdf", "unreferenced document")
    export = write(home / "cache" / "research" / "notes.md", "user export")
    record = legacy_record(reports=[str(evidence)])
    before = (record / "decision.json").read_bytes()
    plan = local_storage.migrate()
    assert not plan["applied"] and secret.exists() and record.exists()
    assert "test-token" not in json.dumps(plan)
    local_storage.migrate(apply=True)
    moved = home / "journal" / record.name
    assert (moved / "record.json").read_bytes() == before
    assert not record.exists() and not secret.exists()
    assert config.credential() == ("test-token", "credentials_file")
    assert config.mode() == "public"
    assert json.loads((state_dir() / "akshare/health.json").read_text()) == {"last_check_at": 100}
    if os.name != "nt":
        assert (config_dir() / "credentials.env").stat().st_mode & 0o777 == 0o600
    # Execution caches and health schemas are not user data and cannot be migrated as valid responses.
    assert (old / "cache/example/health.json").exists()
    assert (old / "cache/example/response.json").exists()
    assert (old / "akshare_snapshots/snapshot.json").exists()
    assert not (state_dir() / "hithink").exists()
    assert not (cache_dir() / "api").exists()
    assert evidence.read_text() == "source evidence" and export.exists()
    assert not report.exists() and (cache_dir() / "documents/reports/report.pdf").exists()
    assert local_storage.migrate(apply=True)["moves"] == []
    event = write(
        home / "event.json",
        {
            "type": "research_updated",
            "source": "user_explicit",
            "note": "补充查证",
            "patch": {"details": {"verified": True}},
        },
    )
    assert journal.main(["append", "--record-dir", str(moved), "--event", str(event)]) == 0
    revised = json.loads((moved / "record.json").read_text())
    original = json.loads(before)
    assert (
        revised["capture"] == original["capture"] and revised["events"][0] == original["events"][0]
    )


@pytest.mark.parametrize("conflict", ["config", "record", "duplicate_id"])
def test_migration_preflights_all_collisions_without_moving_anything(conflict):
    home = default_scutio_home()
    source = write(home / "data_sources/settings.json", {"mode": "public"})
    record = legacy_record()
    if conflict == "config":
        write(config_dir() / "settings.json", {"mode": "auto"})
    elif conflict == "record":
        write(home / "journal" / record.name / "record.json", {})
    else:
        legacy_record(record.name, code="600519")
    with pytest.raises(ValueError, match="already exists|duplicated"):
        local_storage.migrate(apply=True)
    assert source.exists() and (record / "decision.json").exists()


def test_migration_rolls_back_completed_moves_on_io_failure(monkeypatch):
    home = default_scutio_home()
    settings = write(home / "data_sources/settings.json", {"mode": "public"})
    record = legacy_record()
    rename = Path.rename

    def fail(source, target):
        if source.name == "decision.json":
            raise OSError("simulated disk failure")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError, match="simulated"):
        local_storage.migrate(apply=True)
    assert settings.exists() and (record / "decision.json").exists()
    assert not (config_dir() / "settings.json").exists()


def test_migration_refuses_symlinks(tmp_path):
    source = default_scutio_home() / "data_sources/settings.json"
    outside = write(tmp_path / "outside.json", {"mode": "public"})
    source.parent.mkdir(parents=True)
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="symbolic"):
        local_storage.migrate(apply=True)
    assert outside.exists() and source.is_symlink()


def test_legacy_configuration_and_journal_require_migration(monkeypatch, capsys):
    monkeypatch.delenv("SCUTIO_CONFIG_DIR")
    write(default_scutio_home() / "data_sources/settings.json", {"mode": "public"})
    with pytest.raises(ValueError, match="legacy configuration"):
        config.settings()
    legacy_record()
    assert journal.main(["list"]) == 2
    assert "local_storage.py migrate" in capsys.readouterr().err
    local_storage.migrate(apply=True)
    assert config.mode() == "public"
    assert journal.main(["list"]) == 0


def test_setting_legacy_configuration_does_not_create_a_conflicting_lock(monkeypatch):
    monkeypatch.delenv("SCUTIO_CONFIG_DIR")
    legacy = default_scutio_home() / "data_sources"
    write(legacy / "settings.json", {"mode": "auto"})
    write(legacy / "settings.lock", "")
    with pytest.raises(ValueError, match="legacy configuration"):
        config.set_setting("mode", "public")
    assert not (config_dir() / "settings.lock").exists()
    local_storage.migrate(apply=True)
    config.set_setting("mode", "public")
    assert config.mode() == "public"


def test_custom_journal_root_migrates_without_rewriting_history(tmp_path, capsys):
    root = tmp_path / "investment notes"
    record = legacy_record(root=root)
    before = (record / "decision.json").read_bytes()
    attachment = write(record / "attachments" / "source.txt", "original evidence")
    assert journal.main(["--root", str(root), "list"]) == 2
    error = capsys.readouterr().err
    assert "--journal-root" in error and str(root) in error and "--apply" in error
    assert local_storage.main(["migrate", "--journal-root", str(root)]) == 0
    assert (record / "decision.json").exists()
    assert local_storage.main(["migrate", "--journal-root", str(root), "--apply"]) == 0
    moved = root / record.name
    assert (moved / "record.json").read_bytes() == before
    assert (moved / "attachments" / attachment.name).read_text() == "original evidence"
    assert not (root / "decisions").exists()
    assert journal.main(["--root", str(root), "list"]) == 0
    assert local_storage.migrate(apply=True, journal_root=root)["moves"] == []


def test_custom_root_migration_still_protects_default_journal_evidence(tmp_path):
    evidence = write(cache_dir() / "filings" / "source.txt", "original evidence")
    default_record = legacy_record(reports=[str(evidence)])
    custom_root = tmp_path / "custom_journal"
    custom_record = legacy_record(root=custom_root)
    local_storage.migrate(apply=True, journal_root=custom_root)
    assert evidence.exists()
    assert (default_record / "decision.json").exists()
    assert (custom_root / custom_record.name / "record.json").exists()


def test_custom_root_migration_refuses_record_directory_references(tmp_path):
    root = tmp_path / "custom_journal"
    record = legacy_record(root=root, reports=["decisions/_research/20260911-example/source.txt"])
    with pytest.raises(ValueError, match="references to its old directory"):
        local_storage.migrate(apply=True, journal_root=root)
    assert (record / "decision.json").exists()


def test_cache_cleanup_never_touches_state_config_documents_or_journal():
    protected = [
        write(config_dir() / "credentials.env", "private"),
        write(state_dir() / "hithink/test/health.json", {"cooldown": 9999999999}),
        write(cache_dir() / "documents/filings/source.pdf", "source"),
        write(default_scutio_home() / "journal/id/record.json", {"note": "original"}),
    ]
    expired = write(cache_dir() / "api/hithink/test/expired.json", {"expires_at": 1})
    oldest = write(cache_dir() / "api/akshare/old.json", {"expires_at": 9999999999})
    newest = write(cache_dir() / "api/akshare/new.json", {"expires_at": 9999999999})
    os.utime(oldest, (1, 1))
    plan = cache.clean_api_cache(max_bytes=newest.stat().st_size)
    assert len(plan["files"]) == 2 and expired.exists() and oldest.exists()
    cache.clean_api_cache(apply=True, max_bytes=newest.stat().st_size)
    assert not expired.exists() and not oldest.exists() and newest.exists()
    assert all(p.exists() for p in protected)


def test_cache_write_prunes_on_interval_and_survives_readonly_state(monkeypatch):
    clock = [1000]
    monkeypatch.setattr(cache.time, "time", lambda: clock[0])
    target = cache_dir() / "api/akshare/example.json"
    cache.write_api_cache(target, {"items": []}, ttl=1)
    assert json.loads(target.read_text())["expires_at"] == 1001
    first_sweep = (state_dir() / "api_cache.json").read_bytes()
    clock[0] += 5
    another = cache_dir() / "api/akshare/another.json"
    cache.write_api_cache(another, {}, ttl=1000)
    assert target.exists() and (state_dir() / "api_cache.json").read_bytes() == first_sweep
    clock[0] += cache.SWEEP_INTERVAL
    cache.write_api_cache(another, {}, ttl=1000)
    assert not target.exists()
    monkeypatch.setattr(cache, "private_write", Mock(side_effect=PermissionError("readonly")))
    cache.write_api_cache(another, {}, ttl=1000)


def test_storage_cli_preview_does_not_create_local_directories(tmp_path, monkeypatch):
    home = tmp_path / "unused"
    monkeypatch.setenv("SCUTIO_HOME", str(home))
    script = Path(local_storage.__file__)
    for command in ("migrate", "clean-cache"):
        result = subprocess.run(
            [sys.executable, str(script), command], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["applied"] is False
    assert not home.exists()


def test_migration_preserves_relative_document_references():
    evidence = write(cache_dir() / "filings" / "original.txt", "original")
    legacy_record(reports=["cache/filings/original.txt"])
    result = local_storage.migrate(apply=True)
    assert evidence.exists()
    assert result["retained"][0]["reason"] == "referenced_by_journal"


def test_migration_keeps_journal_locks_and_removes_only_empty_old_directories():
    home = default_scutio_home()
    write(home / "data_sources/settings.lock", "")
    write(home / "data_sources/akshare_health.lock", "")
    write(home / "data_sources/cache/test/request.lock", "")
    write(home / "journal/.locks/old.lock", "")
    legacy_record()
    local_storage.migrate(apply=True)
    assert (home / "data_sources/cache/test/request.lock").exists()
    assert not (home / "journal/decisions").exists()
    assert (home / "journal/.locks/old.lock").exists()
    assert (config_dir() / "settings.lock").exists()
    assert not (state_dir() / "settings.lock").exists()
    assert (state_dir() / "akshare/health.lock").exists()
    assert not (state_dir() / "hithink").exists()
    assert not (state_dir() / "journal").exists()
