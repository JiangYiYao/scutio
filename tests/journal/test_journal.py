"""Offline contracts for the minimal Scutio decision journal."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "scutio" / "scripts" / "journal.py"


def run_journal(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def create_decision(tmp_path: Path) -> Path:
    payload = {
        "instrument": {"code": "hk09992", "name": "泡泡玛特", "market": "HK"},
        "raw_user_note": "我最近加了一次仓，先帮我记下来。",
        "as_of": "2026-09-03",
        "details": {"reason": "等待经营验证"},
    }
    input_path = tmp_path / "create.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = run_journal("--root", str(tmp_path / "journal"), "create", "--input", str(input_path))
    assert result.returncode == 0, result.stderr
    return Path(result.stdout.strip())


def test_create_writes_minimal_draft_artifacts(tmp_path: Path):
    target = create_decision(tmp_path)

    assert target.parent == tmp_path / "journal"
    assert {path.name for path in target.iterdir()} == {
        "record.json",
        "memo.md",
    }
    decision = json.loads((target / "record.json").read_text(encoding="utf-8"))
    assert decision["state"] == "DRAFT"
    assert decision["instrument"]["code"] == "hk09992"
    assert decision["capture"]["confirmed"] is False
    assert len(decision["events"]) == 1
    assert decision["events"][0]["type"] == "decision_created"
    assert "退出" not in json.dumps(decision, ensure_ascii=False)
    assert not (tmp_path / "journal" / "index.json").exists()


def test_append_can_activate_without_semantic_gate(tmp_path: Path):
    target = create_decision(tmp_path)
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "type": "decision_updated",
                "note": "用户确认先按当前信息激活，退出条件以后再补。",
                "source": "user_explicit",
                "confirmed": True,
                "patch": {"state": "ACTIVE", "details": {"next_review": "下一财报"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_journal(
        "--root",
        str(tmp_path / "journal"),
        "append",
        "--record-dir",
        str(target),
        "--event",
        str(event_path),
    )
    assert result.returncode == 0, result.stderr
    decision = json.loads((target / "record.json").read_text(encoding="utf-8"))
    assert decision["state"] == "ACTIVE"
    assert decision["details"]["next_review"] == "下一财报"
    assert len(decision["events"]) == 2
    assert decision["events"][-1]["note"] == "用户确认先按当前信息激活，退出条件以后再补。"
    assert "退出条件以后再补" in (target / "memo.md").read_text(encoding="utf-8")


def test_append_rejects_identity_rewrite(tmp_path: Path):
    target = create_decision(tmp_path)
    event_path = tmp_path / "bad-event.json"
    event_path.write_text(
        json.dumps(
            {
                "type": "decision_updated",
                "note": "错误地改写标的",
                "source": "assistant_inference",
                "patch": {"instrument": {"code": "usAAPL"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_journal(
        "--root",
        str(tmp_path / "journal"),
        "append",
        "--record-dir",
        str(target),
        "--event",
        str(event_path),
    )
    assert result.returncode == 2
    assert "immutable" in result.stderr
    decision = json.loads((target / "record.json").read_text(encoding="utf-8"))
    assert len(decision["events"]) == 1


def test_append_allows_name_correction_but_rejects_invalid_projection(tmp_path: Path):
    target = create_decision(tmp_path)
    correction = tmp_path / "correction.json"
    correction.write_text(
        json.dumps(
            {
                "type": "identity_corrected",
                "note": "补充英文名。",
                "source": "user_explicit",
                "confirmed": True,
                "patch": {"instrument": {"name_en": "Pop Mart"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = run_journal(
        "--root",
        str(tmp_path / "journal"),
        "append",
        "--record-dir",
        str(target),
        "--event",
        str(correction),
    )
    assert result.returncode == 0, result.stderr
    decision = json.loads((target / "record.json").read_text(encoding="utf-8"))
    assert decision["instrument"]["name_en"] == "Pop Mart"

    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps(
            {
                "type": "decision_updated",
                "note": "错误结构。",
                "source": "assistant_inference",
                "patch": {"details": "not-an-object"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = run_journal(
        "--root",
        str(tmp_path / "journal"),
        "append",
        "--record-dir",
        str(target),
        "--event",
        str(invalid),
    )
    assert result.returncode == 2
    assert "details must be an object" in result.stderr


def test_create_rejects_unsafe_code(tmp_path: Path):
    payload = {
        "instrument": {"code": "../../escape", "name": "错误标的"},
        "raw_user_note": "不应创建",
    }
    input_path = tmp_path / "unsafe.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = run_journal("--root", str(tmp_path / "journal"), "create", "--input", str(input_path))
    assert result.returncode == 2
    assert "instrument.code" in result.stderr
    assert not (tmp_path / "escape").exists()


def test_list_returns_current_projection_paths(tmp_path: Path):
    target = create_decision(tmp_path)
    result = run_journal("--root", str(tmp_path / "journal"), "list")

    assert result.returncode == 0, result.stderr
    records = json.loads(result.stdout)
    assert records == [
        {
            "decision_id": target.name,
            "instrument": {"code": "hk09992", "market": "HK", "name": "泡泡玛特"},
            "path": str(target),
            "state": "DRAFT",
            "updated_at": records[0]["updated_at"],
        }
    ]


def create_research(tmp_path: Path) -> Path:
    payload = {
        "record_kind": "research",
        "subject": "原料降价如何影响同行",
        "as_of": "2026-08-31",
        "raw_user_note": "先记下这个问题，我还没有交易决定。",
        "details": {
            "claim": {"value": "利润可能改善", "source": "assistant_inference"},
            "open_question": "降价会不会传导给客户",
        },
    }
    input_path = tmp_path / "research.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = run_journal("--root", str(tmp_path / "journal"), "create", "--input", str(input_path))
    assert result.returncode == 0, result.stderr
    return Path(result.stdout.strip())


def test_research_without_instrument_or_decision_can_be_saved_and_listed(tmp_path):
    target = create_research(tmp_path)
    record = json.loads((target / "record.json").read_text(encoding="utf-8"))
    assert record["schema_version"] == "2.1"
    assert record["record_kind"] == "research" and record["instrument"] is None
    assert "action" not in record["details"]
    assert record["events"][0]["type"] == "research_recorded"
    result = run_journal("--root", str(tmp_path / "journal"), "list")
    listed = json.loads(result.stdout)[0]
    assert listed["subject"] == record["subject"]
    assert listed["record_kind"] == "research"
    shown = run_journal("--root", str(tmp_path / "journal"), "show", "--record-dir", str(target))
    assert json.loads(shown.stdout) == record
    assert "研究记录" in (target / "memo.md").read_text(encoding="utf-8")


def test_research_revision_preserves_initial_ai_claim(tmp_path):
    target = create_research(tmp_path)
    initial = json.loads((target / "record.json").read_text(encoding="utf-8"))
    event_path = tmp_path / "revision.json"
    event_path.write_text(
        json.dumps(
            {
                "type": "claim_revised",
                "source": "assistant_inference",
                "note": "合同随原料价调整，原先推断遗漏了客户议价能力。",
                "patch": {
                    "as_of": "2026-09-06",
                    "details": {"claim": {"value": "目前不足以支持利润改善"}},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = run_journal(
        "--root",
        str(tmp_path / "journal"),
        "append",
        "--record-dir",
        str(target),
        "--event",
        str(event_path),
    )
    assert result.returncode == 0, result.stderr
    revised = json.loads((target / "record.json").read_text(encoding="utf-8"))
    assert revised["details"]["claim"]["value"] == "目前不足以支持利润改善"
    assert revised["events"][0] == initial["events"][0]
    assert revised["events"][0]["snapshot"]["details"] == initial["details"]
    assert revised["events"][0]["snapshot"]["as_of"] == "2026-08-31"
    assert revised["as_of"] == "2026-09-06"
    assert revised["capture"] == initial["capture"]
    assert len(revised["events"]) == 2
    memo = (target / "memo.md").read_text(encoding="utf-8")
    assert "利润可能改善" in memo and "目前不足以支持利润改善" in memo


@pytest.mark.parametrize(
    "patch",
    [
        {"capture": {"raw_user_note": "换一种原话"}},
        {"events": []},
        {"record_kind": "decision"},
        {"subject": "改写原问题"},
    ],
)
def test_cannot_overwrite_research_history(tmp_path, patch):
    target = create_research(tmp_path)
    before = (target / "record.json").read_bytes()
    event_path = tmp_path / "rewrite.json"
    event_path.write_text(
        json.dumps({"type": "rewrite", "note": "rewrite", "patch": patch}), encoding="utf-8"
    )
    result = run_journal(
        "--root",
        str(tmp_path / "journal"),
        "append",
        "--record-dir",
        str(target),
        "--event",
        str(event_path),
    )
    assert result.returncode == 2 and "immutable" in result.stderr
    assert (target / "record.json").read_bytes() == before


def test_legacy_v2_record_remains_readable_and_appendable(tmp_path):
    target = create_decision(tmp_path)
    path = target / "record.json"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy["schema_version"] = "2.0"
    legacy["events"][0].pop("snapshot")
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    event_path = tmp_path / "legacy-event.json"
    event_path.write_text(
        json.dumps(
            {"type": "review", "note": "追加复盘", "patch": {"details": {"later": "new evidence"}}}
        ),
        encoding="utf-8",
    )
    result = run_journal(
        "--root",
        str(tmp_path / "journal"),
        "append",
        "--record-dir",
        str(target),
        "--event",
        str(event_path),
    )
    assert result.returncode == 0, result.stderr
    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["schema_version"] == "2.0"
    assert updated["events"][0] == legacy["events"][0]
    assert updated["details"]["reason"] == legacy["details"]["reason"]
    assert updated["details"]["later"] == "new evidence"


@pytest.mark.parametrize("subject", ["", None, {"invalid": "object"}])
def test_invalid_research_subject_does_not_create_record(tmp_path, subject):
    input_path = tmp_path / "bad-research.json"
    input_path.write_text(
        json.dumps({"record_kind": "research", "subject": subject, "raw_user_note": "记录问题"}),
        encoding="utf-8",
    )
    result = run_journal("--root", str(tmp_path / "journal"), "create", "--input", str(input_path))
    assert result.returncode == 2 and "subject" in result.stderr
    assert not list((tmp_path / "journal").rglob("record.json"))


_CONCURRENT_RUNNER = """
import importlib.util, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
spec = importlib.util.spec_from_file_location("journal_under_test", sys.argv[1])
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)
load = journal.load_json
build = journal.build_decision
def delayed_load(path):
    result = load(path)
    if path.name == "record.json":
        time.sleep(0.2)
    return result
def delayed_build(*args):
    time.sleep(0.2)
    return build(*args)
journal.load_json = delayed_load
journal.build_decision = delayed_build
Path(sys.argv[2]).write_text("ready")
# Both processes reach the same starting gate before either writes a record.
sys.stdin.readline()
sys.exit(journal.main(sys.argv[3:]))
"""


def run_concurrent_journal(tmp_path, commands, *, separate_homes=False):
    import time

    processes = []
    try:
        for index, command in enumerate(commands):
            ready = tmp_path / f"ready-{index}"
            env = os.environ.copy()
            if separate_homes:
                home = tmp_path / f"home-{index}"
                env.update(SCUTIO_HOME=str(home), SCUTIO_CONFIG_DIR=str(home / "config"))
            process = subprocess.Popen(
                [sys.executable, "-c", _CONCURRENT_RUNNER, str(SCRIPT), str(ready), *command],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            processes.append((process, ready))
        deadline = time.monotonic() + 10
        while not all(ready.exists() for _, ready in processes):
            assert time.monotonic() < deadline, "journal workers did not reach start gate"
            time.sleep(0.01)
        for process, _ in processes:
            process.stdin.write("start\n")
            process.stdin.flush()
        results = []
        for process, _ in processes:
            out, err = process.communicate(timeout=10)
            results.append((process.returncode, out, err))
        return results
    finally:
        for process, _ in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()


@pytest.mark.parametrize("separate_homes", [False, True])
def test_concurrent_appends_keep_both_events_and_matching_memo(tmp_path, separate_homes):
    target = create_decision(tmp_path)
    commands = []
    for index in (1, 2):
        event = tmp_path / f"event-{index}.json"
        event.write_text(
            json.dumps(
                {
                    "type": "research_reviewed",
                    "note": f"parallel-note-{index}",
                    "patch": {"details": {f"update{index}": True}},
                }
            )
        )
        commands.append(
            [
                "--root",
                str(tmp_path / "journal"),
                "append",
                "--record-dir",
                str(target),
                "--event",
                str(event),
            ]
        )
    results = run_concurrent_journal(tmp_path, commands, separate_homes=separate_homes)
    assert all(code == 0 for code, _, _ in results), results
    decision = json.loads((target / "record.json").read_text())
    assert len(decision["events"]) == 3
    assert decision["details"]["update1"] and decision["details"]["update2"]
    memo = (target / "memo.md").read_text()
    assert "parallel-note-1" in memo and "parallel-note-2" in memo
    # Rendering from the final source of truth must produce identical content.
    assert (
        run_journal(
            "--root", str(tmp_path / "journal"), "render", "--record-dir", str(target)
        ).returncode
        == 0
    )
    assert (target / "memo.md").read_text() == memo


@pytest.mark.parametrize("separate_homes", [False, True])
def test_concurrent_create_cannot_overwrite_same_record(tmp_path, separate_homes):
    payload = tmp_path / "create.json"
    payload.write_text(
        json.dumps(
            {
                "record_kind": "research",
                "subject": "test",
                "raw_user_note": "original",
                "decision_id": "same",
            }
        )
    )
    command = ["--root", str(tmp_path / "journal"), "create", "--input", str(payload)]
    results = run_concurrent_journal(tmp_path, [command, command], separate_homes=separate_homes)
    assert sorted(code for code, _, _ in results) == [0, 2], results
    assert any("already exists" in err for _, _, err in results)


@pytest.mark.parametrize("code", ["usBRK.B", "BRK.B.US", "hk00700", "600519"])
def test_journal_accepts_supported_security_codes_and_can_append(tmp_path, code):
    payload = {"instrument": {"code": code, "name": "Company"}, "raw_user_note": "保存判断"}
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    result = run_journal("--root", str(tmp_path / "journal"), "create", "--input", str(input_path))
    assert result.returncode == 0, result.stderr
    target = Path(result.stdout.strip())
    record = json.loads((target / "record.json").read_text())
    assert record["instrument"]["code"] == code
    assert "." not in record["decision_id"]
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"type": "research_updated", "note": "新增依据", "source": "user_explicit"})
    )
    appended = run_journal(
        "--root",
        str(tmp_path / "journal"),
        "append",
        "--record-dir",
        str(target),
        "--event",
        str(event),
    )
    assert appended.returncode == 0, appended.stderr
