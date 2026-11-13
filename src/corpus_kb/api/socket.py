"""Socket adapter — JSON-RPC 2.0 over Unix socket / Windows named pipe.

Newline-delimited JSON-RPC 2.0 protocol. Each request is one JSON
object per line, each response is one JSON object per line.

Error codes:
  -32600: Invalid request
  -32601: Method not found
  -32602: Invalid params (caller input is wrong)
  -32001: Not found
  -32002: Conflict
  -32003: Database unavailable
  -32004: Migration missing
  -32005: Dependency missing
  -32603: Internal error

Every ``CorpusError`` subclass answers its own ``socket_code`` (issue
#304, the ERROR_MAP contract). Plain exceptions stay -32603.

The method table lives in ``api/socket_ops.py``: one arm per catalogue
operation, core arms ported from the old dispatch chain plus generated
arms over the shared HTTP tool-route table. This module owns the server:
connection handling, request parsing, and the error mapping. The
per-server ENDPOINT (resolution + liveness probe, issue #348) lives in
``api/socket_target.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from corpus_kb.api.socket_ops import MethodNotFoundError
from corpus_kb.api.socket_target import _probe_live
from corpus_kb.errors import ConflictError, CorpusError

logger = logging.getLogger(__name__)

# Caller-input taxonomy classes log at WARNING; server-side classes log
# at ERROR (momus fold, PR #308). The split rides the wire code, so the
# level and the client-visible code can not drift apart.
_CALLER_INPUT_CODES = frozenset({-32602, -32001})

# Platform-specific socket path
if sys.platform == "win32":
    DEFAULT_SOCKET_PATH = r"\\.\pipe\corpus-kb"
else:
    DEFAULT_SOCKET_PATH = "/tmp/corpus-kb.sock"


class JSONRPCServer:
    """JSON-RPC 2.0 server over Unix socket / named pipe.

    ``socket_path`` is the resolved endpoint from
    :func:`corpus_kb.api.socket_target.resolve_socket_target`: a unix
    socket file path (POSIX) or a TCP port number (Windows).
    """

    def __init__(self, socket_path: str | int = DEFAULT_SOCKET_PATH) -> None:
        self._socket_path = socket_path
        self._running = False
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start listening for connections.

        Before touching an existing endpoint the server probes it: when
        another server answers, start raises ``ConflictError`` with one
        clear line (issue #348) — it never takes a live socket over.
        """
        self._running = True

        if sys.platform == "win32":
            # Windows named pipe — use asyncio with a custom protocol
            # For Windows, we use a TCP socket on localhost as fallback
            # since named pipes in asyncio require platform-specific code.
            # The resolved target is that TCP port (int); a bare str here
            # is the legacy default construction and keeps port 8011.
            port = self._socket_path if isinstance(self._socket_path, int) else 8011
            if await _probe_live(port):
                raise ConflictError(
                    f"Another Corpus-KB server is already listening on TCP port {port}. "
                    "Stop it, or run this server on a different --port."
                )
            logger.info("Socket server starting on TCP port: %d", port)
            try:
                self._server = await asyncio.start_server(
                    self._handle_client, host="127.0.0.1", port=port
                )
            except OSError as exc:
                raise ConflictError(
                    f"Cannot listen on TCP port {port}: {exc}. "
                    "Stop the program that holds it, or run on a different --port."
                ) from exc
        else:
            # Unix socket
            socket_path = Path(str(self._socket_path))
            if socket_path.exists():
                if await _probe_live(str(socket_path)):
                    raise ConflictError(
                        f"Another Corpus-KB server is already listening on {socket_path}. "
                        "Stop it, or run this server on a different --port."
                    )
                # No live server answered: the file is stale wreckage from
                # a crashed process. Safe to remove and rebind.
                socket_path.unlink()
            logger.info("Socket server starting on: %s", self._socket_path)
            self._server = await asyncio.start_unix_server(
                self._handle_client, path=str(socket_path)
            )

        logger.info("Socket server listening")

    async def stop(self) -> None:
        """Stop the server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Socket server stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break

                response = await self._process_request(line.decode().strip())
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
        except Exception as exc:
            logger.error("Socket client error: %s", exc)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _process_request(self, raw: str) -> dict[str, Any]:
        """Process a single JSON-RPC request."""
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            return _error(-32600, "Invalid JSON", req_id=None)

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if not method:
            return _error(-32601, "Method not found", req_id)

        try:
            result = await self._dispatch(method, params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except MethodNotFoundError:
            # Same error an absent method gets: unknown methods must map to
            # -32601 (Method not found), never to -32603 (internal error).
            return _error(-32601, "Method not found", req_id)
        except CorpusError as exc:
            # Issue #304: every taxonomy class answers its own socket_code.
            # One failure class maps to one code (the ERROR_MAP contract).
            # Caller-input classes answer -32602 (issue #238 contract).
            if exc.socket_code in _CALLER_INPUT_CODES:
                logger.warning("RPC method %s rejected: %s", method, exc)
            else:
                logger.error("RPC method %s failed: %s", method, exc)
            return _error(exc.socket_code, str(exc), req_id)
        except Exception as exc:
            logger.error("RPC method %s failed: %s", method, exc)
            return _error(-32603, str(exc), req_id)

    async def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one RPC method through the shared arm table."""
        from corpus_kb.api.socket_ops import dispatch

        return await dispatch(method, params)


def _error(code: int, message: str, req_id: Any = None) -> dict[str, Any]:
    """Build a JSON-RPC error response."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


# Singleton

_socket_server: JSONRPCServer | None = None


def get_socket_server(socket_path: str | int = DEFAULT_SOCKET_PATH) -> JSONRPCServer:
    global _socket_server
    if _socket_server is None:
        _socket_server = JSONRPCServer(socket_path)
    return _socket_server


def reset_socket_server() -> None:
    global _socket_server
    _socket_server = None
