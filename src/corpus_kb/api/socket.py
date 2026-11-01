"""Socket adapter — JSON-RPC 2.0 over Unix socket / Windows named pipe.

Newline-delimited JSON-RPC 2.0 protocol. Each request is one JSON
object per line, each response is one JSON object per line.

Error codes:
  -32600: Invalid request
  -32601: Method not found
  -32602: Invalid params (taxonomy ValidationError; issue #238 moved the
          junk-numeric-param failures here from -32603)
  -32603: Internal error

The method table lives in ``api/socket_ops.py``: one arm per catalogue
operation, core arms ported from the old dispatch chain plus generated
arms over the shared HTTP tool-route table. This module owns the server:
connection handling, request parsing, and the error mapping.
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
from corpus_kb.errors import ValidationError

logger = logging.getLogger(__name__)

# Platform-specific socket path
if sys.platform == "win32":
    DEFAULT_SOCKET_PATH = r"\\.\pipe\corpus-kb"
else:
    DEFAULT_SOCKET_PATH = "/tmp/corpus-kb.sock"


class JSONRPCServer:
    """JSON-RPC 2.0 server over Unix socket / named pipe."""

    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH) -> None:
        self._socket_path = socket_path
        self._running = False
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start listening for connections."""
        self._running = True

        if sys.platform == "win32":
            # Windows named pipe — use asyncio with a custom protocol
            logger.info("Socket server starting on named pipe: %s", self._socket_path)
            # For Windows, we use a TCP socket on localhost as fallback
            # since named pipes in asyncio require platform-specific code
            self._server = await asyncio.start_server(
                self._handle_client, host="127.0.0.1", port=8011
            )
        else:
            # Unix socket
            socket_path = Path(self._socket_path)
            if socket_path.exists():
                socket_path.unlink()
            logger.info("Socket server starting on: %s", self._socket_path)
            self._server = await asyncio.start_unix_server(
                self._handle_client, path=self._socket_path
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
        except ValidationError as exc:
            # Issue #238, approved contract change: bad caller input (for
            # example a junk limit) is invalid params (-32602), not an
            # internal error. Other taxonomy classes still ride the
            # generic -32603 catch below (issue #239 scope).
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


def get_socket_server(socket_path: str = DEFAULT_SOCKET_PATH) -> JSONRPCServer:
    global _socket_server
    if _socket_server is None:
        _socket_server = JSONRPCServer(socket_path)
    return _socket_server


def reset_socket_server() -> None:
    global _socket_server
    _socket_server = None
