"""Build a self-contained skill archive from Git-listed files and tested dependencies."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
SKILL = Path("skills/scutio")
VERSION_PATTERN = r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode("utf-8").strip()


def dependency_snapshot(requirements):
    """Resolve installed runtime dependencies, including active markers and extras."""
    pending = [
        Requirement(line.split("#", 1)[0].strip())
        for line in requirements.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    versions = {}
    visited = set()
    while pending:
        req = pending.pop()
        if req.marker and not req.marker.evaluate():
            continue
        name = canonicalize_name(req.name)
        version = importlib.metadata.version(name)
        if req.specifier and not req.specifier.contains(version):
            raise ValueError(f"Installed {name}=={version} does not satisfy {req}")
        versions[name] = version
        key = (name, tuple(sorted(req.extras)))
        if key in visited:
            continue
        visited.add(key)
        for declaration in importlib.metadata.requires(name) or []:
            child = Requirement(declaration)
            if child.marker and not any(
                child.marker.evaluate({"extra": extra}) for extra in {"", *req.extras}
            ):
                continue
            # Marker was evaluated for the parent extras; do not re-evaluate it as a root.
            child.marker = None
            pending.append(child)
    return dict(sorted(versions.items()))


def source_files(root):
    raw = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return sorted({Path(name) for name in raw.split("\0") if name})


def check_public_files(root, files):
    """Check current text artifacts only; this does not inspect or rewrite Git history."""
    patterns = {
        "personal home path": re.compile(
            r"(?<![\w/])(?:/Users/|/home/)[\w.-]+/|[A-Za-z]:\\+Users\\+[\w.-]+\\+"
        ),
        "credential": re.compile(
            r"sk-fuyao-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|"
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
    }
    failures = []
    for relative in files:
        if relative.suffix not in {".md", ".json", ".yaml", ".yml", ".txt", ".py", ".sh", ".ps1"}:
            continue
        path = root / relative
        if not path.is_file():
            continue
        contents = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            # Decode escaped newlines and paths in archived conversation strings.
            def strings(value):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict):
                    for key, item in value.items():
                        yield key
                        yield from strings(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from strings(item)

            contents = "\n".join(strings(json.loads(contents)))
        for label, pattern in patterns.items():
            if pattern.search(contents):
                failures.append(f"{relative}: {label}")
    if failures:
        raise ValueError(
            "Review public artifacts (matched values omitted):\n" + "\n".join(failures)
        )


def validate_tag(root, version, revision, tag):
    """A release tag must name this version and resolve to the checked-out commit."""
    if not re.fullmatch(VERSION_PATTERN, version) or tag != f"v{version}":
        raise ValueError("Release tag must exactly match v + skills/scutio/VERSION")
    try:
        target = git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    except subprocess.CalledProcessError as exc:
        raise ValueError("Release tag is missing from the checkout") from exc
    if target != revision:
        raise ValueError("Release tag does not point to the checked-out commit")


def build(root, output, *, allow_dirty=False, tag=None):
    revision = git(root, "rev-parse", "HEAD")
    dirty = bool(git(root, "status", "--porcelain"))
    if dirty and not allow_dirty:
        raise ValueError(
            "Working tree is dirty; commit release inputs or use --allow-dirty locally"
        )
    files = source_files(root)
    check_public_files(root, files)
    source = root / SKILL
    version = (source / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError("Invalid VERSION; expected X.Y.Z or X.Y.Z-alpha/beta/rc.N")
    if tag is not None:
        if dirty:
            raise ValueError("Tagged releases require a clean working tree")
        validate_tag(root, version, revision, tag)
    payload = {}
    for relative in files:
        if not relative.is_relative_to(SKILL):
            continue
        path = root / relative
        # No linked source or local caches in the distributable, even if accidentally staged.
        parts = relative.relative_to(SKILL).parts
        if any(
            part.startswith(".") or part in {"__pycache__", "cache", "journal"} for part in parts
        ):
            raise ValueError(f"Unexpected runtime/hidden file in skill: {relative}")
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError(f"Linked file cannot be packaged: {relative}")
        if path.suffix in {".pyc", ".pyo"}:
            raise ValueError(f"Compiled cache cannot be packaged: {relative}")
        payload[(Path("scutio") / relative.relative_to(SKILL)).as_posix()] = path.read_bytes()
    for required in (
        "SKILL.md",
        "VERSION",
        "LICENSE",
        "THIRD_PARTY.md",
        "requirements.txt",
        "scripts/runtime_probe.py",
    ):
        if f"scutio/{required}" not in payload:
            raise ValueError(f"Missing release input: {required}")
    versions = dependency_snapshot(payload["scutio/requirements.txt"].decode("utf-8"))
    payload["scutio/tested-constraints.txt"] = (
        "# Resolved runtime environment used to build this preview; see release-manifest.json.\n"
        + "\n".join(f"{name}=={version}" for name, version in versions.items())
        + "\n"
    ).encode("utf-8")
    manifest = {
        "schema_version": 1,
        "version": version,
        "revision": revision,
        "dirty": dirty,
        "build_environment": {"python": platform.python_version(), "system": platform.system()},
        "runtime_dependencies": versions,
        "files": {
            name: hashlib.sha256(value).hexdigest() for name, value in sorted(payload.items())
        },
        "scope": "Hashes cover skill files and tested constraints; manifest excludes itself. Build metadata alone does not attest tests passed.",
    }
    payload["scutio/release-manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"scutio-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, value in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, value)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    archive.with_suffix(".manifest.json").write_bytes(payload["scutio/release-manifest.json"])
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", help="Require this tag to match VERSION and the clean checkout")
    parser.add_argument(
        "--allow-dirty", action="store_true", help="Local preview only; records dirty=true"
    )
    parser.add_argument(
        "--check-public", action="store_true", help="Only scan current Git-listed text files"
    )
    args = parser.parse_args()
    try:
        if args.check_public:
            check_public_files(ROOT, source_files(ROOT))
            print("Current public artifacts: checked (Git history not scanned)")
        else:
            print(build(ROOT, args.output, allow_dirty=args.allow_dirty, tag=args.tag))
    except (ValueError, importlib.metadata.PackageNotFoundError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
