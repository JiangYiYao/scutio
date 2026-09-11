"""pytest 钩子：收集结果并写出人类可读 Markdown 报表。

环境变量：
  SCUTIO_TEST_REPORT=0     关闭写报表（默认写）
  SCUTIO_TEST_REPORT_DIR   报表目录（默认 $SCUTIO_HOME/cache/test_reports）
  SCUTIO_HOME              用户级根（默认 ~/.scutio）
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from case_catalog import KIND_LABEL, STATUS_LABEL, lookup

# 仓库布局：tests → 仓库根 → skills/scutio/scripts
_TESTS = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS.parent
_SCRIPTS = _REPO_ROOT / "skills" / "scutio" / "scripts"
for directory in (_SCRIPTS, _SCRIPTS / "collectors"):
    if directory.is_dir() and str(directory) not in sys.path:
        sys.path.insert(0, str(directory))


def pytest_configure(config: pytest.Config) -> None:
    config._scutio_results: list[dict[str, Any]] = []  # type: ignore[attr-defined]
    config._scutio_actuals: dict[str, str] = {}  # type: ignore[attr-defined]
    config.addinivalue_line("markers", "live: hits public network (opt-in)")


@pytest.fixture(autouse=True)
def _isolate_source_pref(monkeypatch, tmp_path, request):
    """每个 case 独立 source_pref，避免 last_ok 污染与写用户 ~/.scutio。"""
    if request.node.get_closest_marker("live") is None:
        import subprocess

        original_run = subprocess.run

        def offline_worker_guard(args, *a, **k):
            if any(str(arg).endswith(("_akshare_worker.py", "_http_worker.py")) for arg in args):
                raise RuntimeError("offline tests must mock the network worker boundary")
            return original_run(args, *a, **k)

        monkeypatch.setattr(subprocess, "run", offline_worker_guard)
    monkeypatch.setenv("SCUTIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SCUTIO_CONFIG_DIR", str(tmp_path / "home" / "config"))
    monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
    monkeypatch.delenv("SCUTIO_DATA_MODE", raising=False)
    monkeypatch.delenv("SCUTIO_AKSHARE_NETWORK", raising=False)
    monkeypatch.setenv("SCUTIO_AKSHARE_UPDATE_CHECK", "0")
    from scutio_data._providers import eastmoney as providers_eastmoney

    monkeypatch.setattr(providers_eastmoney, "_em_us_market_cache", {})
    pref_path = tmp_path / "source_pref.json"
    monkeypatch.setenv("SCUTIO_SOURCE_PREF_PATH", str(pref_path))
    monkeypatch.setenv("SCUTIO_SOURCE_PREF", "1")
    # 单元测试使用确定性的进程内时钟；跨进程文件锁由 live/部署默认开启。
    monkeypatch.setenv("SCUTIO_EM_CROSS_PROCESS_RATE", "0")
    monkeypatch.setenv("SCUTIO_EM_RATE_STATE_PATH", str(tmp_path / "em_rate_limit.state"))
    try:
        from scutio_data import source_pref

        source_pref.reset_runtime_state()
        source_pref.clear_pref()
    except Exception:
        pass
    yield
    try:
        from scutio_data import source_pref

        source_pref.reset_runtime_state()
    except Exception:
        pass


@pytest.fixture
def record_actual(request: pytest.FixtureRequest):
    """测试内记录「实际输出」摘要，写入人类可读报表。"""

    def _record(text: str) -> None:
        store: dict = request.config._scutio_actuals  # type: ignore[attr-defined]
        store[request.node.nodeid] = str(text)

    return _record


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call" and not (rep.when == "setup" and rep.skipped):
        # 记录 call 阶段；setup skip（如 live 门控）也记
        if not (rep.skipped and rep.when == "setup"):
            return
    status = "passed"
    if rep.skipped:
        status = "skipped"
    elif rep.failed:
        status = "error" if rep.when != "call" else "failed"
    longrepr = ""
    if getattr(rep, "longrepr", None) is not None:
        longrepr = str(rep.longrepr)
    # setup-skip 与 call 都可能写；同 nodeid 以 call 覆盖 setup
    store: list = item.config._scutio_results  # type: ignore[attr-defined]
    entry = {
        "nodeid": item.nodeid,
        "status": status,
        "duration": getattr(rep, "duration", 0.0) or 0.0,
        "longrepr": longrepr,
        "wasxfail": bool(getattr(rep, "wasxfail", False)),
    }
    for i, old in enumerate(store):
        if old["nodeid"] == item.nodeid:
            # call 结果覆盖 setup skip 记录；若 call 未跑则保留 skip
            if rep.when == "call" or old["status"] == "skipped":
                store[i] = entry
            break
    else:
        store.append(entry)


def _clean_skip_reason(longrepr: str) -> str:
    reason = (longrepr or "").strip() or "条件未满足"
    # 常见形态: "('…/test.py', 8, 'Skipped: reason')" 或 "Skipped: reason"
    if "Skipped:" in reason:
        reason = reason.split("Skipped:", 1)[-1].strip()
    reason = reason.strip("()[]'\" \t")
    if reason.endswith("')") or reason.endswith('")'):
        reason = reason[:-2].rstrip("'\"")
    return reason or "条件未满足"


def _display_name(nodeid: str, base_name: str) -> str:
    """参数化 nodeid 尽量显示可读参数，避免整段二进制。"""
    if "[" not in nodeid:
        return base_name
    param = nodeid.rsplit("::", 1)[-1]
    if "[" not in param:
        return base_name
    inside = param[param.index("[") + 1 : param.rindex("]")]
    if not inside:
        return f"{base_name}[empty]"
    # 不可打印 / 过长 → 用短摘要
    printable = sum(1 for c in inside if c.isprintable() and ord(c) >= 32)
    if len(inside) > 48 or printable < max(1, int(len(inside) * 0.7)):
        return f"{base_name}[param={len(inside)}B]"
    return f"{base_name}[{inside}]"


def _actual_summary(
    status: str,
    longrepr: str,
    kind: str,
    captured: str | None = None,
) -> str:
    if status == "passed":
        if captured:
            return captured
        if kind == "live_network":
            return "断言通过（真实返回，报表未捕获摘要）"
        return "与期望一致（断言通过）"
    if status == "skipped":
        return f"未执行：{_clean_skip_reason(longrepr)}"
    # failed / error — 优先展示已捕获的线上摘要
    lines = [ln for ln in longrepr.strip().splitlines() if ln.strip()]
    tail = "\n".join(lines[-12:]) if lines else "(无 traceback)"
    if captured:
        return f"线上摘要：{captured}\n与期望不符：\n```\n{tail}\n```"
    return f"与期望不符：\n```\n{tail}\n```"


def _write_report(config: pytest.Config) -> Path | None:
    if os.getenv("SCUTIO_TEST_REPORT", "1").strip() in {"0", "false", "no"}:
        return None
    results: list[dict[str, Any]] = getattr(config, "_scutio_results", [])
    if not results and not getattr(config.option, "collectonly", False):
        # 仍写空汇总，便于发现「没跑到」
        pass

    scutio_home = Path(os.getenv("SCUTIO_HOME", str(Path.home() / ".scutio"))).expanduser()
    default_report = scutio_home / "cache" / "test_reports"
    report_dir = Path(os.getenv("SCUTIO_TEST_REPORT_DIR", str(default_report))).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"scutio_data_{stamp}.md"
    latest = report_dir / "scutio_data_latest.md"

    actuals: dict[str, str] = getattr(config, "_scutio_actuals", {}) or {}
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0, "xfailed": 0, "xpassed": 0}
    enriched = []
    for r in results:
        meta = lookup(r["nodeid"])
        st = r["status"]
        if r.get("wasxfail") and st == "skipped":
            st = "xfailed"
        counts[st] = counts.get(st, 0) + 1
        enriched.append(
            {
                **r,
                **meta,
                "status": st,
                "captured": actuals.get(r["nodeid"]),
            }
        )

    # 模块汇总
    by_mod: dict[str, dict[str, int]] = {}
    for e in enriched:
        m = e.get("module") or "未分类"
        bucket = by_mod.setdefault(m, {"passed": 0, "failed": 0, "skipped": 0, "error": 0})
        if e["status"] == "error":
            bucket["error"] += 1
        elif e["status"] == "skipped" or e["status"] == "xfailed":
            bucket["skipped"] += 1
        elif e["status"] == "passed" or e["status"] == "xpassed":
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1

    total = sum(counts.values())
    lines: list[str] = []
    lines.append("# Scutio Data 测试报表")
    lines.append("")
    lines.append("| 项 | 内容 |")
    lines.append("|----|------|")
    lines.append(f"| **生成时间** | {ts}（本地） |")
    lines.append("| **范围** | `tests` · `scutio_data` |")
    lines.append(
        f"| **汇总** | 通过 {counts.get('passed', 0)} · "
        f"失败 {counts.get('failed', 0) + counts.get('error', 0)} · "
        f"跳过 {counts.get('skipped', 0) + counts.get('xfailed', 0)} · "
        f"合计 {total} |"
    )
    lines.append(
        f"| **环境** | Python {platform.python_version()} · {platform.system()} "
        f"{platform.release()} · pytest {pytest.__version__} |"
    )
    lines.append(f"| **LIVE** | SCUTIO_LIVE={os.getenv('SCUTIO_LIVE', '')!r} |")
    lines.append(f"| **命令** | `{' '.join(sys.argv)}` |")
    lines.append("")
    lines.append("### 按模块汇总")
    lines.append("")
    lines.append("| 模块 | 通过 | 失败 | 跳过/其他 |")
    lines.append("|------|------|------|-----------|")
    for mod in sorted(by_mod.keys()):
        b = by_mod[mod]
        other = b["skipped"] + b["error"]
        lines.append(f"| {mod} | {b['passed']} | {b['failed']} | {other} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 明细")
    lines.append("")

    # 失败优先，再按 module + name
    def sort_key(e: dict) -> tuple:
        prio = 0 if e["status"] in {"failed", "error"} else (1 if e["status"] == "skipped" else 2)
        return (prio, e.get("module") or "", e.get("name") or e["nodeid"])

    for i, e in enumerate(sorted(enriched, key=sort_key), 1):
        status_l = STATUS_LABEL.get(e["status"], e["status"])
        kind_l = KIND_LABEL.get(e.get("kind") or "", e.get("kind") or "")
        title = _display_name(e["nodeid"], e.get("name") or e["nodeid"])
        # 参数化：把可读参数补进「输入」
        input_text = e.get("input") or ""
        if "[" in e["nodeid"] and title.endswith("]"):
            param_note = title[title.index("[") :]
            if param_note not in input_text:
                input_text = f"{input_text} · 本例参数 {param_note}".strip(" ·")
        lines.append(f"### {i}. `{title}`")
        lines.append("")
        lines.append("| 字段 | 内容 |")
        lines.append("|------|------|")
        lines.append(f"| **结果** | {status_l} |")
        lines.append(f"| **类型** | {kind_l} |")
        lines.append(f"| **模块** | {e.get('module', '')} |")
        lines.append(f"| **测什么** | {e.get('feature', '')} |")
        lines.append(f"| **入口** | `{e.get('entry', '')}` |")
        lines.append(f"| **输入** | {input_text} |")
        lines.append(f"| **期望输出** | {e.get('expected', '')} |")
        actual = _actual_summary(
            e["status"],
            e.get("longrepr") or "",
            e.get("kind") or "",
            e.get("captured"),
        )
        if e["status"] in {"failed", "error"} or "\n" in actual:
            lines.append("| **实际输出** | 见下方 |")
            lines.append(f"| **耗时** | {e.get('duration', 0):.3f}s |")
            lines.append(f"| **nodeid** | `{e['nodeid']}` |")
            lines.append("")
            lines.append(actual)
            lines.append("")
        else:
            # markdown 表格内竖线转义
            safe = actual.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| **实际输出** | {safe} |")
            lines.append(f"| **耗时** | {e.get('duration', 0):.3f}s |")
            lines.append(f"| **nodeid** | `{e['nodeid']}` |")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- **离线**：不访问公网；mock / fixture / 纯函数。")
    lines.append("- **真实公网**：使用 `SCUTIO_LIVE=1`。")
    lines.append(
        "- case 业务说明来自 `tests/case_catalog.py`；分组说明展示文件覆盖范围，具体输入与断言见 nodeid 对应源码。"
    )
    lines.append(f"- 本文件：`{path}`；同目录 `scutio_data_latest.md` 为最新副本。")
    lines.append("")

    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    # 终端提示
    print(f"\n[scutio] 测试报表已写入: {path}")
    print(f"[scutio] 最新副本: {latest}")
    return path


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    try:
        _write_report(session.config)
    except Exception as exc:  # noqa: BLE001 — 报表失败不影响测试 exit code 语义之外再抛
        print(f"[scutio] 写测试报表失败: {exc}", file=sys.stderr)
