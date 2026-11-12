"""One stdlib HTTP seam for every study-server call (issue #351).

``urllib`` only. Every caller gets typed errors with a response body
excerpt, so commands print one honest line instead of a traceback.

Stdlib only.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request


class HttpError(Exception):
    """A user-facing HTTP failure (bad status or no connection)."""


def request_json(
    url: str,
    method: str = "GET",
    payload: dict | list | None = None,
    timeout: float = 120.0,
) -> dict:
    """Send one JSON request and return the parsed JSON body.

    The URL checks run here, in the same body as the request, and refuse
    anything that is not a plain http(s) endpoint before it starts: no
    other schemes, no credentials, a named host, and a loopback host
    name that must resolve to a loopback address. The base URL is
    operator configuration, so a configured non-loopback host is legal.
    The request then goes to the URL rebuilt from the checked parts, so
    the bytes on the wire cannot differ from what was validated. The
    checks mirror ``tests/live_services.py::loopback_service_url`` in
    the engine repo; this module is stdlib-only, so the twin is inlined
    here on purpose.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise HttpError(f"Refused URL {url!r}: it does not parse.") from exc
    if parsed.scheme != "http" and parsed.scheme != "https":
        raise HttpError(f"Refused URL {url!r}: the scheme must be http or https.")
    if parsed.username is not None or parsed.password is not None:
        raise HttpError(f"Refused URL {url!r}: it must not carry credentials.")
    host = (parsed.hostname or "").strip("[]").lower()
    if host == "":
        raise HttpError(f"Refused URL {url!r}: it names no host.")
    if host == "localhost" or host == "127.0.0.1" or host == "::1":
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise HttpError(f"Cannot reach {url}: the host does not resolve.") from exc
        for info in infos:
            address = str(info[4][0])
            if address != "127.0.0.1" and address != "::1":
                raise HttpError(f"Refused URL {url!r}: a loopback name must resolve loopback.")
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    checked_url = urllib.parse.urlunsplit(parsed)
    req = urllib.request.Request(checked_url, data=data, method=method, headers=headers)
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
