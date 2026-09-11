"""One HTTP transfer. Input and output use pipes; the parent owns cancellation."""

import base64
import json
import sys
from pathlib import Path

import requests


def transfer(payload):
    options = dict(payload["options"])
    if isinstance(options.get("timeout"), list):
        options["timeout"] = tuple(options["timeout"])
    with requests.Session() as session:
        session.trust_env = payload["trust_env"]
        session.headers.update(payload["headers"])
        session.cookies.update(payload.get("cookies") or {})
        with session.request(payload["method"], payload["url"], **options) as response:
            return {
                "status": response.status_code,
                "headers": dict(response.headers),
                "encoding": response.encoding,
                "url": response.url,
                "cookies": session.cookies.get_dict(),
                "body": base64.b64encode(response.content).decode("ascii"),
            }


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scutio_data._runtime.processes import install_worker_guard

    install_worker_guard()
    try:
        result = transfer(json.load(sys.stdin))
    except requests.RequestException as exc:
        result = {"error_type": type(exc).__name__}
    except Exception:
        result = {"error_type": "RequestException"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
