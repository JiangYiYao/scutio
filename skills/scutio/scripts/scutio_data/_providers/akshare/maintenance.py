"""Failure-triggered, advisory AKShare update checks. Never installs packages."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = "scutio_data._providers.akshare"

from scutio_data._runtime import config  # noqa: E402 - standalone subprocess bootstrap
from scutio_data.paths import state_dir  # noqa: E402

THRESHOLD = 3


CHECK_INTERVAL = 86400


FAILURE_WINDOW = 3600


def enabled():
    return os.environ.get("SCUTIO_AKSHARE_UPDATE_CHECK", "1").strip().lower() not in (
        "0",
        "false",
        "off",
    )


def installed_version():
    try:
        return version("akshare")
    except PackageNotFoundError:
        return None


def _path():
    return state_dir() / "akshare" / "health.json"


@contextmanager
def _locked():
    """Nonblocking process lock; optional maintenance never delays a data call."""
    path = _path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as stream:
        if sys.platform == "win32":
            import msvcrt

            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)

            def acquire():
                return msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                return (stream.seek(0), msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1))
        else:
            import fcntl

            def acquire():
                return fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                return fcntl.flock(stream, fcntl.LOCK_UN)

        try:
            acquire()
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            release()


def _save(state):
    config.private_write(_path(), json.dumps(state))


def _eligible(error):
    if error is None or error.status in (400, 401, 403, 407, 429):
        return False
    return (
        error.code == "invalid_response"
        or (
            error.code == "provider_error"
            and error.error_type
            in ("KeyError", "IndexError", "AttributeError", "JSONDecodeError", "ValueError")
        )
        or error.code == "upstream_api_error"
    )


def status():
    current = installed_version()
    state = config.read_json(_path())
    if state.get("installed_version") != current:
        state = {}
    check = state.get("check", {})
    if check.get("state") == "pending" and time.time() - check.get("requested_at", 0) > 120:
        check = {**check, "state": "check_failed"}
    return {
        "enabled": enabled(),
        "installed_version": current,
        "policy": "check_only",
        "check": check,
        "failures": state.get("failures", {}),
    }


def observe(function, error=None):
    """Count one complete adapter call, excluding network/auth/rate failures."""
    if not enabled():
        return None
    try:
        now = time.time()
        current = installed_version()
        launch = False
        with _locked() as locked:
            if not locked:
                return None
            state = config.read_json(_path())
            previous = copy.deepcopy(state)
            if state.get("installed_version") != current:
                state = {"installed_version": current}
            failures = state.setdefault("failures", {})
            if not _eligible(error):
                failures.pop(function, None)
            else:
                old = failures.get(function, {})
                count = (
                    old.get("count", 0) if 0 <= now - old.get("last_at", 0) <= FAILURE_WINDOW else 0
                )
                failures[function] = {
                    "count": min(THRESHOLD, count + 1),
                    "last_at": now,
                    "code": error.code,
                    "error_type": error.error_type,
                }
                check = state.get("check", {})
                if count + 1 >= THRESHOLD and (
                    not check or now - check.get("requested_at", 0) >= CHECK_INTERVAL
                ):
                    state["check"] = {"state": "pending", "requested_at": now, "trigger": function}
                    launch = True
            if state != previous:
                _save(state)
        if launch:
            from scutio_data._providers.akshare.network import worker_env

            subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).resolve()), "--background"],
                env=worker_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        check = state.get("check", {})
        if _eligible(error) and failures.get(function, {}).get("count", 0) >= THRESHOLD:
            return check
    except Exception:
        # Optional diagnostics must never change a successful result or its fallback.
        return None
    return None


def _probe():
    """Read official PyPI metadata only; no authentication or source request params."""
    import gzip
    import io
    import urllib.request

    from packaging.specifiers import SpecifierSet
    from packaging.version import InvalidVersion, Version

    request = urllib.request.Request(
        "https://pypi.org/pypi/akshare/json",
        headers={"User-Agent": "scutio-akshare-check", "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
        if getattr(response, "headers", {}).get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("metadata too large")
    data = json.loads(raw)
    candidates = []
    python_version = ".".join(map(str, sys.version_info[:3]))
    for release, files in data.get("releases", {}).items():
        try:
            parsed = Version(release)
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        if any(
            not f.get("yanked")
            and f.get("requires_python")
            and SpecifierSet(f["requires_python"]).contains(python_version)
            for f in files
        ):
            candidates.append(parsed)
    if not candidates:
        raise ValueError("no compatible stable release")
    latest = max(candidates)
    current = installed_version()
    return {
        "state": "update_available" if current and latest > Version(current) else "up_to_date",
        "latest_compatible": str(latest),
        "installed_version": current,
        "fix_verified": False,
        "release_url": "https://pypi.org/project/akshare/" + str(latest) + "/",
        "action": (
            "review release changes and run field/unit regressions before upgrading"
            if current and latest > Version(current)
            else "inspect upstream availability and request parameters"
        ),
    }


def check_now():
    """Bound the entire metadata probe to eight seconds, separate from data budget."""
    from scutio_data._providers.akshare.network import network_mode, worker_env

    current = installed_version()
    mode = network_mode()
    routes = [False, True] if mode == "auto" else [mode == "direct"]
    deadline = time.monotonic() + 8
    payload = {"state": "check_failed"}
    for i, direct in enumerate(routes):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        budget = min(4, remaining) if len(routes) == 2 and i == 0 else remaining
        try:
            run = subprocess.run(
                [sys.executable, "-B", str(Path(__file__).resolve()), "--probe"],
                env=worker_env(direct=direct),
                capture_output=True,
                text=True,
                timeout=budget,
                check=False,
            )
            candidate = json.loads(run.stdout)
            if (
                run.returncode
                or not isinstance(candidate, dict)
                or candidate.get("state") not in ("update_available", "up_to_date")
            ):
                raise ValueError("invalid metadata probe")
            payload = {**candidate, "network": "direct" if direct else "environment"}
            break
        except Exception:
            continue
    with _locked() as locked:
        if not locked:
            return {"state": "busy"}
        state = config.read_json(_path())
        if state.get("installed_version") != current:
            state = {"installed_version": current}
        requested = state.get("check", {})
        state["check"] = {
            **payload,
            "requested_at": time.time(),
            "checked_at": time.time(),
            "trigger": requested.get("trigger"),
        }
        _save(state)
    return state["check"]


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        print(json.dumps(_probe()))
    elif sys.argv[1:] == ["--background"]:
        check_now()
