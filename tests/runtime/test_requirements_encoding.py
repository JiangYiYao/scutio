"""The distributed requirements file must decode under older pip on GBK Windows."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("relative", ["skills/scutio/requirements.txt", "tests/requirements.txt"])
def test_requirements_decode_under_non_utf8_pip(relative):
    requirements = Path(__file__).resolve().parents[2] / relative
    script = """
import locale, sys
from pathlib import Path
from pip._internal.req.req_file import get_file_content
# Also exercise the Windows locale fallback on UTF-8 CI hosts.
locale.getpreferredencoding = lambda do_setlocale=True: 'cp936'
path = Path(sys.argv[1])
data = path.read_bytes()
# Exercise pip's requirements reader, not a decoder moved between pip versions.
_, content = get_file_content(str(path), session=None)
assert content == data.decode('utf-8')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(requirements)],
        env=dict(os.environ, PYTHONUTF8="0", PYTHONIOENCODING="utf-8"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
