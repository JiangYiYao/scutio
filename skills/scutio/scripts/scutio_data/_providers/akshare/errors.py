"""Safe AKShare failure metadata; never return upstream messages or URLs."""


class AKShareError(RuntimeError):
    def __init__(self, code, *, error_type=None, status=None, attempts=None, timeout_seconds=25):
        self.code = code
        self.error_type = error_type
        self.status = status
        self.attempts = list(attempts or [])
        hints = {
            "proxy_error": "proxy connection failed",
            "upstream_connection_error": "upstream connection failed, including possible remote disconnect",
            "upstream_timeout": "upstream request timed out",
            "total_timeout": "total request timeout (%ss)" % timeout_seconds,
            "invalid_response": "worker returned an invalid response",
            "worker_failed": "worker failed; verify Python 3.11+ and runtime dependencies",
        }
        message = "akshare: " + hints.get(code, code)
        if status is not None:
            message += " (HTTP %s)" % status
        if self.attempts:
            message += "; routes: " + ", ".join(
                "%s=%s" % (a["network"], a["code"]) for a in self.attempts
            )
        super().__init__(message)


def safe_failure(exc):
    chain = []
    seen = set()
    current = exc
    status = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(type(current).__name__)
        candidate = getattr(current, "status_code", None)
        response = getattr(current, "response", None)
        if candidate is None and response is not None:
            candidate = getattr(response, "status_code", None)
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and 100 <= candidate <= 599
        ):
            status = candidate
        current = current.__cause__ or current.__context__
    if "ProxyError" in chain:
        code = "proxy_error"
    elif any(name in chain for name in ("SSLError", "SSLCertVerificationError")):
        code = "tls_error"
    elif any(
        name in chain for name in ("Timeout", "ReadTimeout", "ConnectTimeout", "TimeoutError")
    ):
        code = "upstream_timeout"
    elif any(name in chain for name in ("ConnectionError", "NetworkError", "RemoteDisconnected")):
        code = "upstream_connection_error"
    elif status == 429 or "RateLimitError" in chain:
        code = "rate_limited"
    elif "APIError" in chain:
        code = "upstream_api_error"
    elif "HTTPError" in chain:
        code = "upstream_http_error"
    else:
        code = "provider_error"
    return {"code": code, "type": type(exc).__name__, "status": status}
