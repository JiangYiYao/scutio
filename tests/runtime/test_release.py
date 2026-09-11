"""Release archives must work independently and disclose their build provenance."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts/build_release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def test_archive_hashes_and_runtime_survive_standalone_extraction(tmp_path):
    archive = release.build(ROOT, tmp_path / "dist", allow_dirty=True)
    expected = archive.with_suffix(".zip.sha256").read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert all(name.startswith("scutio/") and ".." not in Path(name).parts for name in names)
        manifest = json.loads(bundle.read("scutio/release-manifest.json"))
        assert set(names) == set(manifest["files"]) | {"scutio/release-manifest.json"}
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == digest
        assert "pytest" not in manifest["runtime_dependencies"]
        assert manifest["version"] == bundle.read("scutio/VERSION").decode().strip()
        assert manifest["revision"] == release.git(ROOT, "rev-parse", "HEAD")
        bundle.extractall(tmp_path / "unrelated folder")
    skill = tmp_path / "unrelated folder/scutio"
    env = dict(os.environ, SCUTIO_HOME=str(tmp_path / "user data"), PYTHONDONTWRITEBYTECODE="1")
    for key in ("PYTHONPATH", "SCUTIO_TOOLKIT_SCRIPTS", "SCUTIO_SKILLS_DIR"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-B", str(skill / "scripts/runtime_probe.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert Path(report["scripts"]).resolve() == (skill / "scripts").resolve()
    assert report["scutio_version"] == manifest["version"]
    assert not (tmp_path / "user data").exists()


def test_dependency_manifest_follows_extras_and_rejects_incompatible_versions(monkeypatch):
    installed = {"parent": "1.0", "child": "2.0", "extra-child": "3.0"}
    declarations = {
        "parent": ["child>=2", 'extra-child; extra == "feature"', 'missing; python_version < "3"'],
        "child": [],
        "extra-child": [],
    }
    monkeypatch.setattr(release.importlib.metadata, "version", installed.__getitem__)
    monkeypatch.setattr(release.importlib.metadata, "requires", declarations.__getitem__)
    assert release.dependency_snapshot("parent[feature]==1.0\n") == installed
    assert "extra-child" not in release.dependency_snapshot("parent==1.0")
    with pytest.raises(ValueError, match="does not satisfy"):
        release.dependency_snapshot("parent>=2")


def test_public_scan_preserves_urls_and_reports_paths_without_leaking_values(tmp_path):
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps({"source": "https://example.org/en/home/products/item"}))
    release.check_public_files(tmp_path, [Path("artifact.json")])
    private_path = "/Users" + "/private-name/work/project"
    path.write_text(json.dumps({"trace": "line one\n" + private_path}))
    with pytest.raises(ValueError) as error:
        release.check_public_files(tmp_path, [Path("artifact.json")])
    assert "artifact.json: personal home path" in str(error.value)
    assert private_path not in str(error.value)
