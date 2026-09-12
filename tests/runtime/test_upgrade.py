"""Reinstalling a copied skill preserves existing user artifacts and journal history."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_single_skill_install import _installer


def hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("external_config", [False, True])
def test_copy_upgrade_preserves_data_and_can_append_existing_record(tmp_path, external_config):
    destination = tmp_path / "host skills 中文"
    home = tmp_path / "user data 中文"
    config = tmp_path / "external config 中文" if external_config else home / "config"
    installed = destination / "scutio"
    env = dict(
        os.environ,
        SCUTIO_HOME=str(home),
        SCUTIO_CONFIG_DIR=str(config),
        PYTHONUTF8="0",
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )

    def run(command):
        result = subprocess.run(
            command,
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    run(_installer(destination))
    journal = [sys.executable, "-B", str(installed / "scripts/journal.py")]
    original = "我认为客户验收可能顺利，但目前尚未完成验收。"
    payload = tmp_path / "create.json"
    payload.write_text(
        json.dumps(
            {
                "record_kind": "research",
                "subject": "澄川水务",
                "decision_id": "upgrade-record",
                "raw_user_note": original,
                "details": {"current_view": "验收可能顺利"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record = Path(run([*journal, "create", "--input", str(payload)]).strip())
    before_record = json.loads((record / "record.json").read_text(encoding="utf-8"))
    config.mkdir(parents=True, exist_ok=True)
    (config / "credentials.env").write_text(
        "HITHINK_FINANCE_API_KEY=upgrade-fixture-not-a-real-key\n", encoding="utf-8"
    )
    (config / "settings.json").write_text('{"mode":"public","hint_seen":true}', encoding="utf-8")
    for relative in ("cache/api/fixture.json", "cache/documents/原文.txt", "state/fixture.json"):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"note":"升级前的材料"}', encoding="utf-8")
    (record / "附件.txt").write_text("用户附件", encoding="utf-8")
    # Old installation-only files must disappear, while external user data survives.
    obsolete = installed / "scripts/obsolete-upgrade-fixture.py"
    obsolete.write_text("old installation", encoding="utf-8")
    before_home, before_config = hashes(home), hashes(config)
    for _ in range(2):
        run(_installer(destination))
        assert hashes(home) == before_home
        assert hashes(config) == before_config
        assert not obsolete.exists()
        assert not list(destination.glob(".scutio-install-*"))
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "type": "research_reviewed",
                "note": "升级后复核：设备整改导致验收推迟。",
                "patch": {"details": {"current_view": "验收推迟，需要修正"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run([*journal, "append", "--record-dir", str(record), "--event", str(event)])
    after = json.loads((record / "record.json").read_text(encoding="utf-8"))
    assert after["capture"]["raw_user_note"] == original
    assert after["events"][:-1] == before_record["events"]
    assert after["details"]["current_view"] == "验收推迟，需要修正"
    assert "设备整改" in (record / "memo.md").read_text(encoding="utf-8")
