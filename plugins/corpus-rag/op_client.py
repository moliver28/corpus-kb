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

# The documented default endpoint is the local engine (http://127.0.0.1
# plus the study port). Callers that target a configured remote host add
# that host to the allowlist at the call site; the default stays loopback.
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
_DEFAULT_ALLOWED_HOSTS = frozenset(_LOOPBACK_HOSTS)


class HttpError(Exception):
    """A user-facing HTTP failure (bad status or no connection)."""


def request_json(
    url: str,
    method: str = "GET",
    payload: dict | list | None = None,
    timeout: float = 120.0,
    allowed_hosts: frozenset[str] | None = None,
) -> dict:
    """Send one JSON request and return the parsed JSON body.

    The URL checks run here, in the same body as the request, and refuse
    anything that is not a plain http(s) endpoint before it starts: no
    other schemes, no credentials, and a host inside the allowlist (the
    loopback default, plus the hosts a caller passes as
    ``allowed_hosts``). A loopback name must also resolve to a loopback
    address. A refused URL raises before any bytes leave the process.
    The request goes to the URL rebuilt from the checked parts, so the
    bytes on the wire cannot differ from what was validated.
    """
    allowed = _DEFAULT_ALLOWED_HOSTS
    if allowed_hosts is not None:
        allowed = allowed | frozenset(allowed_hosts)
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise HttpError(f"Refused URL {url!r}: it does not parse.") from exc
    if parsed.scheme != "http" and parsed.scheme != "https":
        raise HttpError(f"Refused URL {url!r}: the scheme must be http or https.")
    if parsed.username is not None or parsed.password is not None:
        raise HttpError(f"Refused URL {url!r}: it must not carry credentials.")
    if parsed.hostname is None or parsed.hostname not in allowed:
        raise HttpError(f"Refused URL {url!r}: the host is not an allowed target.")
    if parsed.hostname in _LOOPBACK_HOSTS:
        try:
            infos = socket.getaddrinfo(parsed.hostname, None, proto=socket.IPPROTO_TCP)
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
