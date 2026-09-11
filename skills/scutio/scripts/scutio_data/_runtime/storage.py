"""JSON 读取与私有文件原子写入。"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: str | Path, timeout_seconds=30):
    """Lock a stable inode for the complete read/modify/write transaction."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b") as stream:
        if os.name == "nt":
            import msvcrt

            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()

            def acquire():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(stream, fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                acquire()
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("configuration is busy; retry the update") from None
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            except OSError as exc:
                if os.name != "nt" or exc.errno not in (13, 11, 36):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("configuration is busy; retry the update") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            release()


def read_json(path: str | Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def private_write(path: str | Path, value: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive temporary file, private from creation; atomic replacement.
    import tempfile

    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
