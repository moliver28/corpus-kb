"""The per-server socket endpoint (issue #348): resolution + liveness probe.

One study = one server = one endpoint. ``resolve_socket_target`` derives
it from the server port (or takes the explicit config key);
``_probe_live`` answers "is another server listening here right now" so
a starting server can refuse instead of taking a live socket over.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable
from typing import Any, cast

# How long the live-socket probe waits for one connect before it calls
# the endpoint dead (issue #348).
_SOCKET_PROBE_TIMEOUT_S = 1.0

# The unix-stream helper exists only on POSIX builds (asyncio defines it
# behind the AF_UNIX guard), so the alias resolves lazily inside the
# POSIX-only probe branch — never at import time.
_UnixConnect = Callable[[str], Awaitable[tuple["asyncio.StreamReader", "asyncio.StreamWriter"]]]


def resolve_socket_target(config: dict[str, Any], platform: str | None = None) -> str | int:
    """The JSON-RPC endpoint for ONE server (issue #348): path or TCP port.

    POSIX names a unix socket file, Windows a TCP port on 127.0.0.1.
    An explicit config key wins: ``server.socket_path`` on POSIX,
    ``server.socket_port`` on Windows. Otherwise the endpoint derives
    from ``server.port``:

    - POSIX: ``/tmp/corpus-kb.sock`` on the default port 8010, else
      ``/tmp/corpus-kb-<port>.sock`` — one path per study server, so a
      second server never takes the first one over.
    - Windows: TCP 8011 on the default port 8010, else ``port + 1000``
      (study ports count up from 8010, so port + 1 would clash with the
      next study).

    ``platform`` is a test seam; None means ``sys.platform``.
    """
    plat = sys.platform if platform is None else platform
    server = config.get("server") if isinstance(config, dict) else None
    server = server if isinstance(server, dict) else {}
    try:
        port = int(server.get("port", 8010))
    except (TypeError, ValueError):
        port = 8010
    if plat == "win32":
        configured = server.get("socket_port") or 0
        try:
            configured = int(configured)
        except (TypeError, ValueError):
            configured = 0
        if configured > 0:
            return configured
        return 8011 if port == 8010 else port + 1000
    path = server.get("socket_path")
    if isinstance(path, str) and path.strip():
        return path
    return "/tmp/corpus-kb.sock" if port == 8010 else f"/tmp/corpus-kb-{port}.sock"


async def _probe_live(target: str | int) -> bool:
    """True when another server answers on ``target`` right now.

    One short connect, then the connection closes. Any failure to
    connect (refused, no file, not a socket, timeout) means the endpoint
    is free — that includes a STALE socket file left by a crashed
    server, which the caller may safely unlink.
    """
    writer: asyncio.StreamWriter | None = None
    try:
        if isinstance(target, int):
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", target), _SOCKET_PROBE_TIMEOUT_S
            )
        else:
            # POSIX only: this branch is unreachable on Windows (the wiring
            # resolves an int there). asyncio defines the unix helper behind
            # the AF_UNIX guard, so resolve it lazily; pyright's stubs omit it.
            connect_unix = cast(_UnixConnect, getattr(asyncio, "open_unix_connection"))  # noqa: B009
            _, writer = await asyncio.wait_for(connect_unix(target), _SOCKET_PROBE_TIMEOUT_S)
    except (OSError, TimeoutError):
        return False
    assert writer is not None
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True
