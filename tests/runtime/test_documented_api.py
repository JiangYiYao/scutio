"""Keep shipped calling examples and the test report catalog aligned with real APIs."""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest
from case_catalog import CASE_GROUPS, CASES, lookup

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/scutio"
REFERENCES = sorted((SKILL / "references/toolkit").glob("*.md"))
EXAMPLES = SKILL / "scripts/examples"


@pytest.mark.parametrize("path", REFERENCES, ids=lambda path: path.stem)
def test_documented_python_imports_and_call_signatures(path):
    for snippet in re.findall(r"```python\n(.*?)```", path.read_text(), re.S):
        tree = ast.parse(snippet, filename=str(path))
        imported = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
                "scutio_data"
            ):
                continue
            module = importlib.import_module(node.module)
            for alias in node.names:
                assert alias.name != "*", "调用示例应显式导入公开入口"
                imported[alias.asname or alias.name] = getattr(module, alias.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            target = imported.get(node.func.id)
            if target is None or not callable(target):
                continue
            if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
                kw.arg is None for kw in node.keywords
            ):
                continue
            # Inspect names/arity without evaluating expressions or executing a data request.
            inspect.signature(target).bind(
                *[None for _ in node.args], **{kw.arg: None for kw in node.keywords}
            )


def test_documented_examples_exist_and_use_public_domain_imports():
    listed = set(re.findall(r"`(\d{2}_[\w]+\.py)`", (EXAMPLES / "README.md").read_text()))
    shipped = {path.name for path in EXAMPLES.glob("[0-9]*.py")}
    assert listed == shipped
    for name in shipped:
        tree = ast.parse((EXAMPLES / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("scutio_data"):
                assert len(node.module.split(".")) == 2, f"{name}: {node.module}"
                module = importlib.import_module(node.module)
                for alias in node.names:
                    assert not alias.name.startswith("_")
                    assert hasattr(module, alias.name), f"{name}: {alias.name}"


def test_catalog_entries_and_groups_reference_existing_tests():
    functions = {}
    for path in (ROOT / "tests").rglob("test_*.py"):
        relative = path.relative_to(ROOT / "tests").as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                functions.setdefault(node.name, []).append(relative)
                assert lookup(f"{relative}::{node.name}")["module"] != "未分类"
    assert not (CASES.keys() - functions.keys())
    # Function-name metadata must never silently describe a same-named test in another file.
    assert not {name: functions[name] for name in CASES if len(functions[name]) > 1}
    assert all((ROOT / "tests" / path).is_file() for path in CASE_GROUPS)


def test_catalog_parameterized_nodeid_retains_its_identity():
    nodeid = (
        "tests/data/test_optional_sources.py::test_full_statement_does_not_shrink_with_key[bank]"
    )
    result = lookup(nodeid)
    assert result["module"] == "可选数据服务"
    assert result["nodeid"] == nodeid
    assert result["name"] == "test_full_statement_does_not_shrink_with_key"


def test_workflow_probe_ids_exist(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests"))
    probes = importlib.import_module("self_check")
    registered = {probe.id for probe in probes.PROBES}
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    selections = re.findall(r"--only\s+([\w,]+)", workflow)
    assert selections
    for selection in selections:
        assert set(selection.split(",")) <= registered


def test_unknown_probe_rejected_before_execution(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests"))
    probes = importlib.import_module("self_check")
    with pytest.raises(ValueError, match="unknown probe IDs"):
        probes.run_self_check(only=["local_split_code", "fund_flow_minute"], pace_sec=0)
    with pytest.raises(SystemExit) as error:
        probes.main(["--only", "fund_flow_minute"])
    assert error.value.code == 2
