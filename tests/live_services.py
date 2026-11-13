"""Loopback guards for the live-service probes (Ollama and friends).

The live probes ride operator config and hard-coded dev hosts. This
module applies the ui recorder rule to every probe URL: http(s) only, no
credentials, a loopback host, and a resolved loopback address. A base
that fails the guard answers ``None``, and callers treat that as "the
service is not usable here" (the test skips), never as a failure.
"""

from __future__ import annotations

import socket
from urllib.parse import urlsplit

__all__ = ["loopback_service_url"]

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_LOOPBACK_ADDRESSES = {"127.0.0.1", "::1"}


def loopback_service_url(base: str, path: str) -> str | None:
    """The probe URL on ``base``, or ``None`` when ``base`` is not local.

    ``path`` starts with a slash and stays inside the validated origin.
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
    return f"{parsed.scheme}://{parsed.netloc}{path}"
