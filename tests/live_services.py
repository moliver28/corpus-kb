"""Loopback guards for the live-service probes (Ollama and friends).

The live probes ride operator config and hard-coded dev hosts. This
module runs the whole probe: the URL checks and the request share one
body, so the request goes to the exact origin this module validated
(http(s) only, no credentials, a loopback host, a resolved loopback
address). A base that fails the guard, or a request that fails, answers
``None``, and callers treat that as "the service is not usable here"
(the test skips), never as a failure.
"""

from __future__ import annotations

import json
import socket
import urllib.request
from urllib.parse import urlsplit

__all__ = ["loopback_probe_status"]

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_LOOPBACK_ADDRESSES = {"127.0.0.1", "::1"}


def loopback_probe_status(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout: float,
) -> int | None:
    """The HTTP status of one probe request, or ``None`` when not usable.

    ``path`` starts with a slash and stays inside the validated origin.
    ``payload`` rides as one JSON body. ``None`` covers a refused base
    and a failed request; callers answer "skip".
    """
    try:
        parsed = urlsplit(base)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").strip("[]").lower()
    if host not in _LOOPBACK_HOSTS:
        return None
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    addresses = {info[4][0] for info in infos}
    if addresses - _LOOPBACK_ADDRESSES:
        return None
    url = f"{parsed.scheme}://{parsed.netloc}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except OSError:
        return None
