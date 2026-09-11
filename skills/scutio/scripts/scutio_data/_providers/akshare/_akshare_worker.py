"""Private subprocess entry point. Only JSON records leave this worker."""

import contextlib
import io
import json
import sys
from pathlib import Path


def main():
    request = json.load(sys.stdin)
    # Import the allowlist without importing the entire AKShare runtime in the parent.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scutio_data._providers.akshare.client import ALLOWED
    from scutio_data._providers.akshare.errors import safe_failure

    if request.get("function") not in ALLOWED:
        raise ValueError("unsupported adapter")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import akshare

            frame = getattr(akshare, request["function"])(**request["params"])
            rows = json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
        print(json.dumps({"ok": True, "items": rows}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "failure": safe_failure(exc)}))


if __name__ == "__main__":
    main()
