"""One stdlib HTTP seam for every study-server call (issue #351).

``urllib`` only. Every caller gets typed errors with a response body
excerpt, so commands print one honest line instead of a traceback.

Stdlib only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class HttpError(Exception):
    """A user-facing HTTP failure (bad status or no connection)."""


def request_json(
    url: str,
    method: str = "GET",
    payload: dict | list | None = None,
    timeout: float = 120.0,
) -> dict:
    """Send one JSON request and return the parsed JSON body."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        except OSError:
            detail = ""
        raise HttpError(f"HTTP {exc.code} from {method} {url}: {detail[:400]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HttpError(f"Cannot reach {method} {url}: {exc}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise HttpError(f"{method} {url} returned a non-JSON body: {body[:200]}") from exc


def get_json(url: str, timeout: float = 120.0) -> dict:
    return request_json(url, "GET", None, timeout)


def post_json(url: str, payload: dict | list, timeout: float = 120.0) -> dict:
    return request_json(url, "POST", payload, timeout)
