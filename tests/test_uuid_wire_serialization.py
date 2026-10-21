"""Wire-serialization regression tests for live UUID-carrying rows.

The route-smoke suite stubs handlers with string-only canned values, so a
pydantic model dumped in python mode (UUID objects kept) reaches the JSON
boundary only against a real database — where ``JSONResponse`` then raises
``Object of type UUID is not JSON serializable`` on every read route. These
tests build the result models with real UUID fields and require every
surface envelope (HTTP, socket, MCP tool) to ``json.dumps`` cleanly.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

import corpus_kb.api.socket_ops as socket_ops
import corpus_kb.handlers.query_crud_reads as query_crud
import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.handlers.query_integrity as query_integrity_module
import corpus_kb.tools.ingest_tools as ingest_tools
from corpus_kb.api.http import create_http_app
from corpus_kb.domain.models import DocumentResult, SearchResult, SQLQuery

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def _search_hit() -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        text="chunk text",
        score=0.5,
        source="doc.md",
        doc_id=uuid4(),
    )


def _document_row() -> DocumentResult:
    return DocumentResult(
        doc_id=uuid4(),
        source="doc.md",
        source_type="markdown",
        chunk_count=3,
        created_at="2026-10-17T00:00:00+00:00",
    )


class _FakeQueryHandler:
    """Answers every query method with real UUID-carrying result models."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls: list[str] = []

    async def handle_search_with_meta(self, query: object) -> object:
        self.calls.append("handle_search_with_meta")
        return query_integrity_module.finalize_envelope([_search_hit()], [])

    async def handle_list_documents(self, query: object) -> list[DocumentResult]:
        self.calls.append("handle_list_documents")
        return [_document_row()]

    async def handle_list_entities(self, query: object) -> list[object]:
        self.calls.append("handle_list_entities")
        return []

    async def handle_search_similar(self, query: object) -> list[SearchResult]:
        self.calls.append("handle_search_similar")
        return [_search_hit()]

    async def handle_search_context(self, query: object) -> list[SearchResult]:
        self.calls.append("handle_search_context")
        return [_search_hit()]


def _client_with_fake_handler(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(query_handler_module, "get_query_handler", lambda: _FakeQueryHandler())
    return TestClient(create_http_app())


def test_envelope_to_payload_serializes_uuids() -> None:
    envelope = query_integrity_module.finalize_envelope([_search_hit()], [])
    payload = query_integrity_module.envelope_to_payload(envelope)
    dumped = json.dumps(payload)
    hit = payload["result"][0]
    assert isinstance(hit["chunk_id"], str)
    UUID(str(hit["chunk_id"]))
    UUID(str(hit["doc_id"]))
    assert "not JSON serializable" not in dumped


@pytest.mark.parametrize(("method", "path"), [("GET", "/api/documents"), ("GET", "/api/entities")])
def test_read_routes_return_json_with_uuid_rows(
    method: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client_with_fake_handler(monkeypatch)
    response = client.request(method, path)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    assert "not JSON serializable" not in response.text


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/search", {"query": "test"}),
        ("POST", "/api/search/similar", {"chunk_id": str(uuid4())}),
        ("POST", "/api/search/context", {"query": "test"}),
    ],
)
def test_search_routes_return_json_with_uuid_rows(
    method: str, path: str, body: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client_with_fake_handler(monkeypatch)
    response = client.request(method, path, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    assert "not JSON serializable" not in response.text


@pytest.mark.asyncio
async def test_socket_list_documents_json_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_handler_module, "get_query_handler", lambda: _FakeQueryHandler())
    envelope = await socket_ops._list_documents({}, DEFAULT_TENANT)
    dumped = json.dumps(envelope)
    assert isinstance(envelope["result"][0]["doc_id"], str)
    assert "not JSON serializable" not in dumped


@pytest.mark.asyncio
async def test_mcp_list_documents_json_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_handler_module, "QueryHandler", _FakeQueryHandler)
    envelope = await ingest_tools.list_documents(pg_pool=object())  # type: ignore[arg-type]
    dumped = json.dumps(envelope)
    assert isinstance(envelope["result"][0]["doc_id"], str)
    assert "not JSON serializable" not in dumped


@pytest.mark.asyncio
async def test_sql_query_rows_are_json_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    row_uuid = uuid4()

    class _FakeConn:
        async def fetch(self, sql: str, *params: object) -> list[dict[str, object]]:
            return [
                {"id": row_uuid, "ts": datetime.now(UTC), "n": 1},
                {"id": str(row_uuid), "nested": {"inner": row_uuid}},
            ]

    @asynccontextmanager
    async def _fake_tenant_connection(pool: object, tenant: str):
        yield _FakeConn()

    monkeypatch.setattr(query_crud, "tenant_connection", _fake_tenant_connection)
    rows = await query_crud.sql_query(
        pool=object(),  # type: ignore[arg-type]
        query=SQLQuery(tenant_id=UUID(int=0), sql="SELECT 1"),
    )
    dumped = json.dumps(rows)
    assert rows[0]["id"] == str(row_uuid)
    assert rows[1]["nested"]["inner"] == str(row_uuid)
    assert "not JSON serializable" not in dumped
