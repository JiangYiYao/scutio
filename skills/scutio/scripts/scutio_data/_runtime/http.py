"""Requests-compatible buffered responses with cancellable transfer deadlines."""

from __future__ import annotations

import base64
import json
import math
import os
import subprocess
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from scutio_data._runtime.timeouts import RequestTimeout, remaining, source_budget


def retry_after(value):
    """Parse Retry-After seconds or an HTTP date; invalid headers wait 30 seconds."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return 30
    return max(1, seconds) if math.isfinite(seconds) else 30


def _worker_exit_error(result):
    """Expose a fixed startup category without returning stderr paths or credentials."""
    known = {
        "ModuleNotFoundError",
        "ImportError",
        "SyntaxError",
        "IndentationError",
        "PermissionError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
    }
    cause = next(
        (
            line.partition(":")[0]
            for line in reversed((result.stderr or "").splitlines())
            if line.partition(":")[0] in known
        ),
        "unknown",
    )
    return requests.RequestException(
        f"HTTP worker exited before returning a response (exit={result.returncode}, cause={cause})"
    )


class Session(requests.Session):
    def request(self, method, url, **kwargs):
        with source_budget():
            seconds = remaining("response")
            specified = kwargs.pop("timeout", None)
            download = kwargs.pop("download", False)
            read = 30 if download else 20
            connect = 5
            if specified is not None:
                if isinstance(specified, (tuple, list)):
                    connect, read = specified
                else:
                    read = min(read, specified)
            kwargs["timeout"] = (min(connect, seconds), min(read, seconds))
            headers = dict(self.headers)
            headers.update(kwargs.pop("headers", None) or {})
            payload = {
                "method": method,
                "url": url,
                "headers": headers,
                "cookies": self.cookies.get_dict(),
                "trust_env": self.trust_env,
                "options": kwargs,
            }
            env = {
                key: value
                for key, value in os.environ.items()
                if not any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET"))
            }
            try:
                result = subprocess.run(
                    # Do not let this directory's http.py shadow stdlib http.client.
                    [sys.executable, "-P", "-B", str(Path(__file__).with_name("_http_worker.py"))],
                    input=json.dumps(payload),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=seconds,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise RequestTimeout("response") from None
            except OSError as exc:
                raise requests.RequestException(
                    f"HTTP worker could not start (errno={exc.errno})"
                ) from None
            remaining("response")
            if result.returncode:
                raise _worker_exit_error(result)
            try:
                data = json.loads(result.stdout)
            except (ValueError, TypeError):
                raise requests.RequestException("HTTP worker returned invalid JSON") from None
            if not isinstance(data, dict):
                raise requests.RequestException("HTTP worker returned an invalid response")
            if data.get("error_type"):
                error = getattr(requests.exceptions, data["error_type"], requests.RequestException)
                if not isinstance(error, type) or not issubclass(error, requests.RequestException):
                    error = requests.RequestException
                raise error(f"HTTP transfer failed ({error.__name__})")
            response = requests.Response()
            response.status_code = data["status"]
            response.headers.update(data["headers"])
            response.encoding = data["encoding"]
            response.url = data["url"]
            response._content = base64.b64decode(data["body"])
            response._content_consumed = True
            response.cookies.update(data["cookies"])
            self.cookies.update(data["cookies"])
            remaining("response")
            return response


def get(url, **kwargs):
    with Session() as session:
        return session.get(url, **kwargs)


def post(url, **kwargs):
    with Session() as session:
        return session.post(url, **kwargs)
