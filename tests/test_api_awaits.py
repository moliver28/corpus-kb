"""Socket/HTTP dispatch honesty pins, started by the five missing-await fixes.

The original five: ingest handlers must actually be awaited in api/http.py
(ingest file/text/directory) and api/socket.py (file/text). A missing await
surfaces as a coroutine object reaching JSONResponse / json.dumps — the route
then 400s (http) or errors -32603 (socket).

The suite now also pins the wave-1B parity contract: all nine socket methods
reach their handler (awaited), ``search`` forwards ``source_type``,
``add_relation`` forwards ``weight``/``metadata``, unknown methods map to
-32601 (never -32603). Fake handlers only; no database.
"""

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
        self.entities: list[Any] = []
        self.relations: list[Any] = []

    async def handle_ingest_file(self, cmd: Any) -> dict[str, object]:
        self.called.append("ingest_file")
        return {"status": "success", "handler": "ingest_file"}

    async def handle_ingest_text(self, cmd: Any) -> dict[str, object]:
        self.called.append("ingest_text")
        return {"status": "success", "handler": "ingest_text"}

    async def handle_ingest_directory(self, cmd: Any) -> dict[str, object]:
        self.called.append("ingest_directory")
        return {"status": "success", "handler": "ingest_directory"}

    def handle_add_entity(self, cmd: Any) -> dict[str, object]:
        self.called.append("add_entity")
        self.entities.append(cmd)
        return {"status": "success", "handler": "add_entity"}

    def handle_add_relation(self, cmd: Any) -> dict[str, object]:
        self.called.append("add_relation")
        self.relations.append(cmd)
        return {"status": "success", "handler": "add_relation"}


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


# ---------------------------------------------------------------------------
# Socket parameter parity with HTTP (wave 1B honesty program)
# ---------------------------------------------------------------------------


class _Dump:
    """Stands in for a pydantic result model."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "python") -> dict[str, object]:
        return dict(self._payload)


class _RecordingQueryHandler:
    """Records SearchQuery / VerifyAnswerQuery / ListDocumentsQuery objects."""

    def __init__(self) -> None:
        self.searches: list[Any] = []
        self.verifies: list[Any] = []
        self.listings: list[Any] = []

    async def handle_search_with_meta(self, query: Any) -> Any:
        from corpus_kb.handlers.query_integrity import DegradedExcluded, SearchEnvelope

        self.searches.append(query)
        return SearchEnvelope(
            status="success",
            results=[],
            degraded_excluded=DegradedExcluded(count=0, reasons=[]),
        )

    async def handle_verify_answer(self, query: Any) -> Any:
        self.verifies.append(query)
        return _Dump({"groundedness": 1.0})

    async def handle_list_documents(self, query: Any) -> list[_Dump]:
        self.listings.append(query)
        return [_Dump({"doc_id": "d1"})]


def _install_query_handler(monkeypatch: Any) -> _RecordingQueryHandler:
    import corpus_kb.handlers.query_handler as query_handler_module

    handler = _RecordingQueryHandler()
    monkeypatch.setattr(query_handler_module, "get_query_handler", lambda: handler)
    return handler


async def test_socket_search_forwards_source_type(monkeypatch: Any) -> None:
    handler = _install_query_handler(monkeypatch)
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "search",
                "params": {"query": "q", "source_type": "transcript"},
            }
        )
    )

    assert response["result"]["status"] == "success"
    assert [str(q.source_type) for q in handler.searches] == ["transcript"]


async def test_socket_search_source_type_defaults_to_none(monkeypatch: Any) -> None:
    handler = _install_query_handler(monkeypatch)
    server = JSONRPCServer()

    await server._process_request(
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "search", "params": {"query": "q"}})
    )

    assert [q.source_type for q in handler.searches] == [None]


async def test_socket_add_relation_forwards_weight_and_metadata(monkeypatch: Any) -> None:
    handler = _install(monkeypatch)
    server = JSONRPCServer()
    src = "00000000-0000-0000-0000-00000000000a"
    dst = "00000000-0000-0000-0000-00000000000b"

    response = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "add_relation",
                "params": {
                    "source_entity_id": src,
                    "target_entity_id": dst,
                    "weight": 0.5,
                    "metadata": {"origin": "test"},
                },
            }
        )
    )

    assert response["result"] == {"status": "success", "handler": "add_relation"}
    cmd = handler.relations[0]
    assert str(cmd.source_entity_id) == src
    assert str(cmd.target_entity_id) == dst
    assert cmd.weight == 0.5
    assert cmd.metadata == {"origin": "test"}


async def test_socket_add_relation_keeps_http_defaults(monkeypatch: Any) -> None:
    handler = _install(monkeypatch)
    server = JSONRPCServer()
    src = "00000000-0000-0000-0000-00000000000a"
    dst = "00000000-0000-0000-0000-00000000000b"

    await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "add_relation",
                "params": {"source_entity_id": src, "target_entity_id": dst},
            }
        )
    )

    cmd = handler.relations[0]
    assert cmd.weight == 1.0
    assert cmd.metadata == {}


async def test_socket_unknown_method_returns_32601() -> None:
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "no_such_method", "params": {}})
    )

    assert response["error"]["code"] == -32601
    assert response["error"]["message"] == "Method not found"


async def test_socket_unknown_method_matches_missing_method_error() -> None:
    server = JSONRPCServer()

    unknown = await server._process_request(
        json.dumps({"jsonrpc": "2.0", "id": 8, "method": "no_such_method", "params": {}})
    )
    missing = await server._process_request(json.dumps({"jsonrpc": "2.0", "id": 8}))

    assert unknown["error"] == missing["error"]


async def test_socket_internal_error_still_32603(monkeypatch: Any) -> None:
    _install_query_handler(monkeypatch)
    server = JSONRPCServer()

    # search without the required "query" param is a genuine internal error,
    # not an unknown method.
    response = await server._process_request(
        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "search", "params": {}})
    )

    assert response["error"]["code"] == -32603


# ---------------------------------------------------------------------------
# Dispatch-await coverage: every socket method must actually await its
# handler/tool (a missing await surfaces as -32603, never a result).
# ---------------------------------------------------------------------------


class _FakeRouterHandler:
    async def handle_routed_query(self, query: Any) -> _Dump:
        return _Dump({"answer": "routed"})


async def test_socket_ingest_url_awaits_tool(monkeypatch: Any) -> None:
    import corpus_kb.api.research_http as research_http_module
    import corpus_kb.tools.ingest_tools as ingest_tools_module

    seen: list[tuple[str, bool]] = []

    async def fake_ingest_url(
        url: str,
        pool: Any,
        tenant_id: str = "t",
        crawl: bool = False,
        source_type: str | None = None,
        validate: str = "off",
    ) -> dict[str, object]:
        seen.append((url, crawl))
        return {"status": "success", "handler": "ingest_url"}

    async def fake_pool() -> object:
        return object()

    monkeypatch.setattr(ingest_tools_module, "ingest_url", fake_ingest_url)
    monkeypatch.setattr(research_http_module, "research_pool", fake_pool)
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "ingest_url",
                "params": {"url": "https://example.com", "crawl": True},
            }
        )
    )

    assert response["result"] == {"status": "success", "handler": "ingest_url"}
    assert seen == [("https://example.com", True)]


async def test_socket_query_awaits_router_handler(monkeypatch: Any) -> None:
    import corpus_kb.handlers.router_handler as router_handler_module

    monkeypatch.setattr(router_handler_module, "get_router_handler", _FakeRouterHandler)
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps({"jsonrpc": "2.0", "id": 11, "method": "query", "params": {"query": "q"}})
    )

    assert response["result"] == {"status": "success", "result": {"answer": "routed"}}


async def test_socket_list_documents_awaits_query_handler(monkeypatch: Any) -> None:
    handler = _install_query_handler(monkeypatch)
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "list_documents",
                "params": {"limit": 5, "offset": 3},
            }
        )
    )

    assert response["result"] == {"status": "success", "result": [{"doc_id": "d1"}]}
    query = handler.listings[0]
    assert query.limit == 5
    assert query.offset == 3


async def test_socket_add_entity_dispatches_to_handler(monkeypatch: Any) -> None:
    handler = _install(monkeypatch)
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "add_entity",
                "params": {"name": "concept-a", "entity_type": "concept"},
            }
        )
    )

    assert response["result"] == {"status": "success", "handler": "add_entity"}
    assert str(handler.entities[0].name) == "concept-a"


async def test_socket_verify_answer_awaits_query_handler(monkeypatch: Any) -> None:
    handler = _install_query_handler(monkeypatch)
    server = JSONRPCServer()

    response = await server._process_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "verify_answer",
                "params": {"answer": "a", "chunk_ids": []},
            }
        )
    )

    assert response["result"] == {"status": "success", "result": {"groundedness": 1.0}}
    assert len(handler.verifies) == 1
