"""An empty lock file already held by another process must not be initialized."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "scutio" / "scripts"

RUNNER = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import journal
from scutio_data._runtime.storage import file_lock
from scutio_data._providers.akshare import maintenance
def waiting(seconds):
    print('waiting', flush=True)
    sys.stdin.readline()
time.sleep = waiting
kind, target = sys.argv[2], Path(sys.argv[3])
if kind == 'journal':
    lock = journal.record_lock(target)
elif kind == 'storage':
    lock = file_lock(target)
else:
    maintenance._path = lambda: target.with_suffix('.json')
    lock = maintenance._locked()
with lock as acquired:
    print('busy' if acquired is False else 'acquired', flush=True)
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range lock semantics")
@pytest.mark.parametrize("kind", ["journal", "storage", "maintenance"])
def test_contended_empty_lock_does_not_write_before_acquiring(tmp_path, kind):
    import msvcrt

    target = tmp_path / "record"
    if kind == "journal":
        digest = hashlib.sha256(str(target.resolve()).encode("utf-8")).hexdigest()
        lock_path = tmp_path / ".locks" / (digest + ".lock")
    elif kind == "maintenance":
        lock_path = target.with_suffix(".lock")
    else:
        lock_path = target
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w+b") as stream:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        process = subprocess.Popen(
            [sys.executable, "-c", RUNNER, str(SCRIPTS), kind, str(target)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        try:
            first = process.stdout.readline().strip()
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        try:
            out, err = process.communicate(input="released\n", timeout=10)
            assert process.returncode == 0, err
            assert first == ("busy" if kind == "maintenance" else "waiting"), (first, out, err)
            if kind != "maintenance":
                assert out.strip() == "acquired"
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
