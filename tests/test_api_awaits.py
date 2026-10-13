"""The five missing-await fixes: ingest handlers must actually be awaited
in api/http.py (ingest file/text/directory) and api/socket.py (file/text).

A missing await surfaces as a coroutine object reaching JSONResponse /
json.dumps — the route then 400s (http) or errors -32603 (socket). These
tests pin the fixed behavior with fake handlers, no database."""

from __future__ import annotations

import json
from typing import Any

from starlette.testclient import TestClient

import corpus_kb.handlers.command_handler as command_handler_module
from corpus_kb.api.http import create_http_app
from corpus_kb.api.socket import JSONRPCServer


class _FakeCommandHandler:
    """Records which handler methods were reached, returns plain dicts."""

    def __init__(self) -> None:
        self.called: list[str] = []

    async def handle_ingest_file(self, cmd: Any) -> dict[str, object]:
        self.called.append("ingest_file")
        return {"status": "success", "handler": "ingest_file"}

    async def handle_ingest_text(self, cmd: Any) -> dict[str, object]:
        self.called.append("ingest_text")
        return {"status": "success", "handler": "ingest_text"}

    async def handle_ingest_directory(self, cmd: Any) -> dict[str, object]:
        self.called.append("ingest_directory")
        return {"status": "success", "handler": "ingest_directory"}


def _install(monkeypatch: Any) -> _FakeCommandHandler:
    handler = _FakeCommandHandler()
    monkeypatch.setattr(command_handler_module, "get_command_handler", lambda: handler)
    return handler


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


def test_http_ingest_file_awaits_handler(monkeypatch: Any) -> None:
    handler = _install(monkeypatch)
    client = TestClient(create_http_app())

    response = client.post("/api/ingest/file", json={"file_path": "/tmp/x.txt"})

    assert response.status_code == 200
    assert response.json() == {"status": "success", "handler": "ingest_file"}
    assert handler.called == ["ingest_file"]


def test_http_ingest_text_awaits_handler(monkeypatch: Any) -> None:
    handler = _install(monkeypatch)
    client = TestClient(create_http_app())

    response = client.post("/api/ingest/text", json={"text": "hello"})

    assert response.status_code == 200
    assert response.json() == {"status": "success", "handler": "ingest_text"}
    assert handler.called == ["ingest_text"]


def test_http_ingest_directory_awaits_handler(monkeypatch: Any) -> None:
    handler = _install(monkeypatch)
    client = TestClient(create_http_app())

    response = client.post("/api/ingest/directory", json={"directory_path": "/tmp"})

    assert response.status_code == 200
    assert response.json() == {"status": "success", "handler": "ingest_directory"}
    assert handler.called == ["ingest_directory"]


# ---------------------------------------------------------------------------
# JSON-RPC socket dispatch
# ---------------------------------------------------------------------------


async def test_socket_ingest_file_awaits_handler(monkeypatch: Any) -> None:
    handler = _install(monkeypatch)
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "ingest_file", "params": {"file_path": "x"}}
        )
    )

    assert response["result"] == {"status": "success", "handler": "ingest_file"}
    assert handler.called == ["ingest_file"]


async def test_socket_ingest_text_awaits_handler(monkeypatch: Any) -> None:
    handler = _install(monkeypatch)
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "ingest_text", "params": {"text": "hello"}}
        )
    )

    assert response["result"] == {"status": "success", "handler": "ingest_text"}
    assert handler.called == ["ingest_text"]
