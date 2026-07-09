"""Socket adapter — JSON-RPC 2.0 over Unix socket / Windows named pipe.

Newline-delimited JSON-RPC 2.0 protocol. Each request is one JSON
object per line, each response is one JSON object per line.

Error codes:
  -32600: Invalid request
  -32601: Method not found
  -32603: Internal error
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Platform-specific socket path
if sys.platform == "win32":
    DEFAULT_SOCKET_PATH = r"\\.\pipe\corpus-kb"
else:
    DEFAULT_SOCKET_PATH = "/tmp/corpus-kb.sock"

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


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
            if os.path.exists(self._socket_path):
                os.unlink(self._socket_path)
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
            try:
                await writer.wait_closed()
            except Exception:
                pass

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
        except Exception as exc:
            logger.error("RPC method %s failed: %s", method, exc)
            return _error(-32603, str(exc), req_id)

    async def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch RPC method to appropriate handler."""
        tenant_id = params.get("tenant_id", DEFAULT_TENANT)

        if method == "ingest_file":
            from handlers.command_handler import get_command_handler
            from domain.models import IngestFileCommand

            cmd = IngestFileCommand(
                tenant_id=UUID(tenant_id),
                file_path=params["file_path"],
                content=params.get("content"),
                source_type=params.get("source_type"),
            )
            return get_command_handler().handle_ingest_file(cmd)

        elif method == "ingest_text":
            from handlers.command_handler import get_command_handler
            from domain.models import IngestTextCommand

            cmd = IngestTextCommand(
                tenant_id=UUID(tenant_id),
                text=params["text"],
                source=params.get("source", "raw_text"),
                source_type=params.get("source_type", "text"),
            )
            return get_command_handler().handle_ingest_text(cmd)

        elif method == "search":
            from handlers.query_handler import get_query_handler
            from domain.models import SearchQuery

            query = SearchQuery(
                tenant_id=UUID(tenant_id),
                query=params["query"],
                k=params.get("k", 10),
            )
            results = await get_query_handler().handle_search(query)
            return {"status": "success", "result": [r.model_dump() for r in results]}

        elif method == "list_documents":
            from handlers.query_handler import get_query_handler
            from domain.models import ListDocumentsQuery

            query = ListDocumentsQuery(
                tenant_id=UUID(tenant_id),
                limit=params.get("limit", 100),
                offset=params.get("offset", 0),
            )
            results = await get_query_handler().handle_list_documents(query)
            return {"status": "success", "result": [r.model_dump() for r in results]}

        elif method == "add_entity":
            from handlers.command_handler import get_command_handler
            from domain.models import AddEntityCommand

            cmd = AddEntityCommand(
                tenant_id=UUID(tenant_id),
                name=params["name"],
                entity_type=params.get("entity_type", "concept"),
                metadata=params.get("metadata", {}),
            )
            return get_command_handler().handle_add_entity(cmd)

        elif method == "add_relation":
            from handlers.command_handler import get_command_handler
            from domain.models import AddRelationCommand

            cmd = AddRelationCommand(
                tenant_id=UUID(tenant_id),
                source_entity_id=UUID(params["source_entity_id"]),
                target_entity_id=UUID(params["target_entity_id"]),
                relation_type=params.get("relation_type", "related_to"),
            )
            return get_command_handler().handle_add_relation(cmd)

        else:
            raise ValueError(f"Method not found: {method}")


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