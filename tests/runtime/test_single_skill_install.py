"""An installed Scutio directory must work without sibling skills or repo paths."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _installer(destination, root=ROOT, mode=None):
    if sys.platform == "win32":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        return [
            shell,
            "-NoProfile",
            "-File",
            str(root / "install.ps1"),
            "-Dest",
            str(destination),
        ] + (["-Mode", mode] if mode else [])
    return ["bash", str(root / "install.sh"), "--dest", str(destination)] + (
        ["--" + mode] if mode else []
    )


@pytest.mark.parametrize("mode", ["link", "copy"])
def test_installer_rejects_destination_containing_checkout(tmp_path, mode):
    checkout = tmp_path / "installed skills/scutio"
    source = checkout / "skills/scutio"
    source.mkdir(parents=True)
    marker = source / "SKILL.md"
    marker.write_text("source must survive", encoding="utf-8")
    (source / "requirements.txt").write_text("", encoding="utf-8")
    for name in ("install.sh", "install.ps1"):
        shutil.copyfile(ROOT / name, checkout / name)
    env = dict(os.environ, SCUTIO_HOME=str(tmp_path / "data"))
    result = subprocess.run(
        _installer(checkout.parent, checkout, mode),
        env=env,
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "source must survive"
    assert (checkout / "install.sh").is_file()


def test_installer_can_replace_existing_link_without_removing_source(tmp_path):
    destination = tmp_path / "installed skills"
    env = dict(os.environ, SCUTIO_HOME=str(tmp_path / "data"))
    for mode in ("link", "copy"):
        result = subprocess.run(
            _installer(destination, mode=mode),
            env=env,
            cwd=tmp_path,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert (ROOT / "skills/scutio/SKILL.md").is_file()
    assert not (destination / "scutio").is_symlink()


@pytest.fixture
def installed_skill(tmp_path):
    destination = tmp_path / "installed skills"
    env = os.environ.copy()
    for key in ("PYTHONPATH", "SCUTIO_SKILLS_DIR", "SCUTIO_TOOLKIT_SCRIPTS"):
        env.pop(key, None)
    env["SCUTIO_HOME"] = str(tmp_path / "user-data")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        _installer(destination), env=env, cwd=tmp_path, capture_output=True, timeout=60
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    discovered = list(destination.rglob("SKILL.md"))
    assert discovered == [destination / "scutio" / "SKILL.md"]
    assert (destination / "scutio").resolve() != (ROOT / "skills/scutio").resolve()
    return destination / "scutio", env


def test_copied_skill_collectors_find_their_own_data_package(installed_skill, tmp_path):
    skill, env = installed_skill
    # Loading and invoking locators checks more than CLI --help, which can defer imports.
    code = """
import importlib.util, json, sys
from pathlib import Path
skill = Path(sys.argv[1])
entries = ["collectors/collect_research_base.py"]
for i, relative in enumerate(entries):
    spec = importlib.util.spec_from_file_location("collector_%s" % i, skill / "scripts" / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found = module._DIRECTORY.parent
    assert Path(found).resolve() == (skill / "scripts").resolve(), (relative, found)
from scutio_data import paths
assert Path(paths.__file__).resolve().is_relative_to(skill.resolve())
print(json.dumps({"scripts": str(paths.find_scripts_dir())}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(skill)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert Path(json.loads(result.stdout)["scripts"]).resolve() == (skill / "scripts").resolve()
    result = subprocess.run(
        [sys.executable, "-B", str(skill / "scripts/runtime_probe.py")],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["status"] == "ready"
    for relative in (
        "journal.py",
        "local_storage.py",
        "collectors/collect_research_base.py",
        "screen_records.py",
        "data_sources.py",
    ):
        result = subprocess.run(
            [sys.executable, str(skill / "scripts" / relative), "--help"],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, (relative, result.stderr)


def test_installer_preserves_legacy_install_and_stops(tmp_path):
    destination = tmp_path / "installed skills"
    legacy = destination / "scutio-research"
    legacy.mkdir(parents=True)
    marker = legacy / "SKILL.md"
    marker.write_text("user edits", encoding="utf-8")
    env = os.environ.copy()
    env["SCUTIO_HOME"] = str(tmp_path / "user-data")
    result = subprocess.run(
        _installer(destination), env=env, cwd=tmp_path, capture_output=True, timeout=60
    )
    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "user edits"
    assert not (destination / "scutio").exists()


def test_installed_reference_links_are_self_contained(installed_skill):
    skill, _ = installed_skill
    documents = [
        skill / "SKILL.md",
        skill / "THIRD_PARTY.md",
        *(skill / "references").rglob("*.md"),
    ]
    for document in documents:
        for target in re.findall(
            r"(?<!!)\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")
        ):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (document.parent / target.split("#")[0]).resolve()
            assert resolved.is_relative_to(skill.resolve()), (document, target)
            assert resolved.exists(), (document, target)


def _venv_options(venv, python, recreate=False):
    if sys.platform == "win32":
        return ["-WithVenv", "-VenvDir", str(venv), "-Python", str(python)] + (
            ["-RecreateVenv"] if recreate else []
        )
    return ["--with-venv", "--venv-dir", str(venv), "--python", str(python)] + (
        ["--recreate-venv"] if recreate else []
    )


def test_invalid_python_leaves_installation_and_environment_untouched(tmp_path):
    destination = tmp_path / "skills"
    installed = destination / "scutio"
    installed.mkdir(parents=True)
    marker = installed / "user-edit.txt"
    marker.write_text("keep", encoding="utf-8")
    venv = tmp_path / "runtime"
    result = subprocess.run(
        _installer(destination) + _venv_options(venv, tmp_path / "missing-python"),
        env=dict(os.environ, SCUTIO_HOME=str(tmp_path / "data")),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert not venv.exists()
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not list(destination.glob(".scutio-install*"))


@pytest.mark.parametrize("recreate", [False, True])
def test_dependency_failure_preserves_skill_and_restores_previous_environment(tmp_path, recreate):
    checkout = tmp_path / "checkout"
    source = checkout / "skills/scutio"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("new skill", encoding="utf-8")
    # No network: force pip to fail on an unavailable package in a new venv.
    (source / "requirements.txt").write_text(
        "scutio-installer-test-missing-package==0.0.0\n", encoding="utf-8"
    )
    for name in ("install.sh", "install.ps1"):
        shutil.copyfile(ROOT / name, checkout / name)
    destination = tmp_path / "installed skills"
    old_skill = destination / "scutio"
    old_skill.mkdir(parents=True)
    (old_skill / "SKILL.md").write_text("old skill", encoding="utf-8")
    venv = tmp_path / "runtime"
    if recreate:
        # Simulate an environment whose base interpreter no longer exists.
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = missing-base\n", encoding="utf-8")
        (venv / "user-marker").write_text("keep", encoding="utf-8")
    env = dict(
        os.environ,
        SCUTIO_HOME=str(tmp_path / "data"),
        PIP_NO_INDEX="1",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PYTHONUTF8="1",
    )
    result = subprocess.run(
        _installer(destination, root=checkout) + _venv_options(venv, sys.executable, recreate),
        env=env,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode != 0
    # Check that failure actually reached pip, not just an earlier argument error.
    assert b"scutio-installer-test-missing-package" in result.stdout + result.stderr
    assert (old_skill / "SKILL.md").read_text(encoding="utf-8") == "old skill"
    if recreate:
        assert (venv / "pyvenv.cfg").read_text(encoding="utf-8") == "home = missing-base\n"
        assert (venv / "user-marker").read_text(encoding="utf-8") == "keep"
    else:
        assert not venv.exists()
    assert not list(tmp_path.glob("runtime.backup-*"))
    assert not list(destination.glob(".scutio-install*"))


def test_venv_cannot_be_placed_inside_skill_installation(tmp_path):
    destination = tmp_path / "skills"
    venv = destination / "scutio/runtime"
    result = subprocess.run(
        _installer(destination) + _venv_options(venv, sys.executable),
        env=dict(os.environ, SCUTIO_HOME=str(tmp_path / "data")),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert not venv.exists()
    assert not (destination / "scutio").exists()


@pytest.mark.parametrize("setting", ["SCUTIO_HOME", "SCUTIO_CONFIG_DIR"])
@pytest.mark.parametrize("with_venv", [False, True])
def test_installer_preserves_user_data_inside_replaced_skill(tmp_path, setting, with_venv):
    destination = tmp_path / "installed skills"
    installed = destination / "scutio"
    data = installed / "user data"
    data.mkdir(parents=True)
    original = data / "record.json"
    original.write_text('{"user_note": "preserve the original"}', encoding="utf-8")
    (installed / "SKILL.md").write_text("previous skill", encoding="utf-8")
    runtime = tmp_path / "external runtime"
    env = dict(
        os.environ,
        SCUTIO_HOME=str(tmp_path / "home"),
        SCUTIO_CONFIG_DIR=str(tmp_path / "config"),
        PIP_NO_INDEX="1",
    )
    env[setting] = str(data)
    result = subprocess.run(
        _installer(destination) + (_venv_options(runtime, sys.executable) if with_venv else []),
        env=env,
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert b"SCUTIO_HOME/SCUTIO_CONFIG_DIR" in result.stdout + result.stderr
    assert original.read_text(encoding="utf-8") == '{"user_note": "preserve the original"}'
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == "previous skill"
    assert not runtime.exists()
    assert not list(destination.glob(".scutio-install*"))


def _directory_alias(link, target):
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            pytest.skip("directory junctions unavailable")
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("setting", ["SCUTIO_HOME", "SCUTIO_CONFIG_DIR"])
@pytest.mark.parametrize("alias_kind", ["data", "destination"])
def test_installer_detects_user_data_overlap_through_directory_alias(tmp_path, setting, alias_kind):
    destination = tmp_path / "installed skills"
    data = destination / "scutio/user data"
    data.mkdir(parents=True)
    marker = data / "user-note.txt"
    marker.write_text("keep", encoding="utf-8")
    alias = tmp_path / "directory alias"
    _directory_alias(alias, data if alias_kind == "data" else destination)
    env = dict(
        os.environ,
        SCUTIO_HOME=str(tmp_path / "home"),
        SCUTIO_CONFIG_DIR=str(tmp_path / "config"),
    )
    env[setting] = str(alias if alias_kind == "data" else data)
    result = subprocess.run(
        _installer(alias if alias_kind == "destination" else destination),
        env=env,
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert b"SCUTIO_HOME/SCUTIO_CONFIG_DIR" in result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8") == "keep"
    assert alias.is_dir()


def test_installer_preserves_config_link_located_inside_replaced_skill(tmp_path):
    destination = tmp_path / "installed skills"
    installed = destination / "scutio"
    installed.mkdir(parents=True)
    external = tmp_path / "external config"
    external.mkdir()
    marker = external / "user-note.txt"
    marker.write_text("keep", encoding="utf-8")
    alias = installed / "config link"
    _directory_alias(alias, external)
    result = subprocess.run(
        _installer(destination),
        env=dict(
            os.environ,
            SCUTIO_HOME=str(tmp_path / "home"),
            SCUTIO_CONFIG_DIR=str(alias),
        ),
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert b"SCUTIO_HOME/SCUTIO_CONFIG_DIR" in result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8") == "keep"
    assert alias.is_dir()


def test_invalid_data_directory_does_not_replace_existing_skill(tmp_path):
    destination = tmp_path / "installed skills"
    installed = destination / "scutio"
    installed.mkdir(parents=True)
    marker = installed / "SKILL.md"
    marker.write_text("previous skill", encoding="utf-8")
    invalid = tmp_path / "not a directory"
    invalid.write_text("keep", encoding="utf-8")
    result = subprocess.run(
        _installer(destination),
        env=dict(os.environ, SCUTIO_HOME=str(invalid)),
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "previous skill"
    assert invalid.read_text(encoding="utf-8") == "keep"


def test_installer_exports_paths_that_survive_a_different_working_directory(tmp_path):
    runtime = tmp_path / "runtime with spaces"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(runtime)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    env = dict(
        os.environ,
        SCUTIO_HOME="state roots/home",
        SCUTIO_CONFIG_DIR="state roots/config",
    )
    options = ["-VenvDir" if sys.platform == "win32" else "--venv-dir", runtime.name]
    result = subprocess.run(
        _installer(tmp_path / "installed skills") + options,
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    script = tmp_path / "check_settings.py"
    script.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "names = ['SCUTIO_HOME', 'SCUTIO_CONFIG_DIR', 'SCUTIO_VENV', 'SCUTIO_PYTHON']\n"
        "Path(os.environ['SCUTIO_HOME'], 'probe.txt').write_text('correct root')\n"
        "print(json.dumps({name: os.environ[name] for name in names}))\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "another task"
    elsewhere.mkdir()
    if sys.platform == "win32":
        exports = [line.strip() for line in result.stdout.splitlines() if "$env:" in line]
        shell = shutil.which("pwsh") or shutil.which("powershell")
        command = [
            shell,
            "-NoProfile",
            "-Command",
            "\n".join(exports) + "\n& $env:SCUTIO_PYTHON $env:SCUTIO_INSTALL_TEST_SCRIPT",
        ]
    else:
        exports = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip().startswith("export ")
        ]
        command = [
            "bash",
            "-c",
            "\n".join(exports) + '\n"$SCUTIO_PYTHON" "$SCUTIO_INSTALL_TEST_SCRIPT"',
        ]
    followup = subprocess.run(
        command,
        cwd=elsewhere,
        env=dict(env, SCUTIO_INSTALL_TEST_SCRIPT=str(script)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert followup.returncode == 0, followup.stderr
    actual = json.loads(followup.stdout)
    expected = {
        "SCUTIO_HOME": tmp_path / "state roots/home",
        "SCUTIO_CONFIG_DIR": tmp_path / "state roots/config",
        "SCUTIO_VENV": runtime,
        "SCUTIO_PYTHON": runtime
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"),
    }
    for name, path in expected.items():
        assert Path(actual[name]).is_absolute()
        assert Path(actual[name]).resolve() == path.resolve()
    assert (expected["SCUTIO_HOME"] / "probe.txt").read_text() == "correct root"
    assert not (elsewhere / "state roots").exists()
