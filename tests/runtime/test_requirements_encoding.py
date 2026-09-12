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
from pip._internal.utils.encoding import auto_decode
# Also exercise the Windows locale fallback on UTF-8 CI hosts.
locale.getpreferredencoding = lambda do_setlocale=True: 'cp936'
path = Path(sys.argv[1])
data = path.read_bytes()
assert auto_decode(data) == data.decode('utf-8')
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
