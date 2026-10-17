"""Route-smoke tests: every declared HTTP route answers with live JSON.

A route whose handler call TypeErrors (a missing required parameter, e.g.
an unsupplied ``pg_pool``) still "runs" — the broad except maps the error
to a 400 — so the route ships dead. Each test here calls one declared
route with stub dependencies (fake pool, stubbed handler callables) and
fails when the response is not parseable JSON or leaks a TypeError
("positional argument") or a traceback. No database is needed: every
dependency seam that the routes already expose is stubbed.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from starlette.routing import Route
from starlette.testclient import TestClient

import corpus_kb.api.research_http as research_http
import corpus_kb.handlers.coding_handler as coding_handler_module
import corpus_kb.handlers.command_handler as command_handler_module
import corpus_kb.handlers.graph_handler as graph_handler_module
import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.handlers.query_integrity as query_integrity_module
import corpus_kb.handlers.release_handlers as release_handlers_module
import corpus_kb.handlers.router_handler as router_handler_module
import corpus_kb.handlers.tag_handler as tag_handler_module
import corpus_kb.handlers.versioning_handler as versioning_handler_module
import corpus_kb.tools.ask_tools as ask_tools
import corpus_kb.tools.ingest_surface_tools as ingest_surface_tools
from corpus_kb.api.http import create_http_app
from corpus_kb.api.research_http import ToolRoute, _tool_routes
from corpus_kb.handlers.coding_handler import CodingHandler
from corpus_kb.handlers.command_handler import CommandHandler
from corpus_kb.handlers.graph_handler import GraphHandler
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.release_handlers import StatusReport
from corpus_kb.handlers.router_handler import RouterHandler
from corpus_kb.handlers.tag_handler import TagHandler
from corpus_kb.handlers.versioning_handler import VersioningHandler

_CANNED = {"status": "success", "stubbed": True}
_DRIFT_MARKERS = ("positional argument", "Traceback")
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


class _FakePool:
    """Identity-only asyncpg pool stub; stubbed handlers never use it."""


class _Dumpable(dict[str, object]):
    """A dict that also answers ``model_dump()`` for pydantic-shaped routes."""

    def model_dump(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return dict(self)


def _body_value(key: str) -> str:
    if key.endswith("_id"):
        return str(uuid4())
    return f"value-{key}"


def _assert_live_response(response: Any) -> dict[str, object]:
    """The route answered with parseable JSON and no leaked TypeError."""
    payload = response.json()
    text = str(payload)
    for marker in _DRIFT_MARKERS:
        assert marker not in text, f"dead route ({response.status_code}): {text}"
    assert response.status_code == 200, f"expected 200, got {response.status_code}: {text}"
    return payload


# ---------------------------------------------------------------------------
# Family A: research/release tool routes (api/research_http._tool_routes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _tool_routes(), ids=lambda r: r.path)
def test_tool_route_returns_live_json(route: ToolRoute, monkeypatch: Any) -> None:
    real = route.fn
    captured: dict[str, object] = {}

    async def stub(*args: object, **kwargs: object) -> dict[str, object]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return dict(_CANNED)

    monkeypatch.setattr(importlib.import_module(str(real.__module__)), str(real.__name__), stub)
    pool = _FakePool()
    monkeypatch.setattr(research_http, "_pool", pool)

    client = TestClient(create_http_app())
    if route.path == "/api/release/status":
        response = client.get(route.path, params={key: _body_value(key) for key in route.required})
    else:
        response = client.post(route.path, json={key: _body_value(key) for key in route.required})

    assert _assert_live_response(response) == _CANNED
    args = captured.get("args", ())
    kwargs = captured.get("kwargs", {})
    if route.pool_kw is not None:
        assert kwargs.get(route.pool_kw) is pool, "route did not supply the pool"
    # The route must supply every required parameter of the real handler.
    inspect.signature(real).bind(*args, **kwargs)


def test_every_tool_route_is_registered_with_expected_method() -> None:
    by_path = {
        r.path: r
        for r in create_http_app().routes
        if isinstance(r, Route) and r.path.startswith("/api/")
    }
    for route in _tool_routes():
        registered = by_path[route.path]
        want = {"GET"} if route.path == "/api/release/status" else {"POST"}
        assert want <= set(registered.methods), route.path


async def test_release_status_get_returns_payload_with_stubbed_pool(monkeypatch: Any) -> None:
    """Regression: GET /api/release/status reaches the shared layer (D1 view)."""
    pool = _FakePool()
    monkeypatch.setattr(research_http, "_pool", pool)
    seen: dict[str, object] = {}

    async def fake_status_report(
        sent_pool: Any, tenant_id: UUID, codebook_version_id: UUID
    ) -> StatusReport:
        seen["pool"] = sent_pool
        seen["tenant_id"] = tenant_id
        seen["codebook_version_id"] = codebook_version_id
        return StatusReport(
            codebook_version_id=str(codebook_version_id),
            current_stage="pre_draft",
            release_state="none",
            blocking_criteria=[],
            recommended_next_action="Run: corpus-kb release assess <version-id>",
            active_release=None,
            draft=None,
            pending_change_proposals=[],
            data_sufficiency={},
        )

    monkeypatch.setattr(release_handlers_module, "status_report", fake_status_report)

    version_id = str(uuid4())
    client = TestClient(create_http_app())
    response = client.get("/api/release/status", params={"codebook_version_id": version_id})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"  # the real StatusReport.to_payload contract
    for key in (
        "codebook_version_id",
        "current_stage",
        "release_state",
        "recommended_next_action",
        "active_release",
        "draft",
        "pending_change_proposals",
    ):
        assert key in payload
    assert seen["pool"] is pool, "route did not pass the pool to the shared layer"
    assert seen["tenant_id"] == UUID(DEFAULT_TENANT)
    assert seen["codebook_version_id"] == UUID(version_id)


# ---------------------------------------------------------------------------
# Family B: handler-singleton routes (api/http.py get_*_handler seams)
# ---------------------------------------------------------------------------


class _FakeHandler:
    """Mirror the real handler: per-method sync/async, JSON-able results."""

    def __init__(self, real: type, shape: str) -> None:
        self.calls: list[str] = []
        self._shape = shape
        for name, member in inspect.getmembers(real, inspect.isfunction):
            if name.startswith("handle_"):
                setattr(self, name, self._bind_method(name, inspect.iscoroutinefunction(member)))

    def _bind_method(self, name: str, is_async: bool) -> Any:
        if is_async:

            async def async_method(*args: object, **kwargs: object) -> object:
                return self._record(name)

            return async_method

        def sync_method(*args: object, **kwargs: object) -> object:
            return self._record(name)

        return sync_method

    def _record(self, name: str) -> object:
        self.calls.append(name)
        if self._shape == "list":
            return [{"method": name}]
        if self._shape == "dumpable_list":
            return [_Dumpable(method=name)]
        if self._shape == "dumpable":
            return _Dumpable(method=name)
        return {"status": "success", "method": name}


@dataclass(frozen=True)
class _Seam:
    module: Any
    factory: str
    real: type


_SEAMS = {
    "command": _Seam(command_handler_module, "get_command_handler", CommandHandler),
    "query": _Seam(query_handler_module, "get_query_handler", QueryHandler),
    "router": _Seam(router_handler_module, "get_router_handler", RouterHandler),
    "graph": _Seam(graph_handler_module, "get_graph_handler", GraphHandler),
    "tag": _Seam(tag_handler_module, "get_tag_handler", TagHandler),
    "versioning": _Seam(versioning_handler_module, "get_versioning_handler", VersioningHandler),
    "coding": _Seam(coding_handler_module, "get_coding_handler", CodingHandler),
}

_HANDLER_ROUTES = [
    pytest.param(
        "POST",
        "/api/ingest/file",
        "command",
        "handle_ingest_file",
        "dict",
        {"file_path": "x.txt"},
        id="POST /api/ingest/file",
    ),
    pytest.param(
        "POST",
        "/api/ingest/text",
        "command",
        "handle_ingest_text",
        "dict",
        {"text": "hello"},
        id="POST /api/ingest/text",
    ),
    pytest.param(
        "POST",
        "/api/ingest/directory",
        "command",
        "handle_ingest_directory",
        "dict",
        {"directory_path": "docs"},
        id="POST /api/ingest/directory",
    ),
    pytest.param(
        "POST",
        "/api/entities",
        "command",
        "handle_add_entity",
        "dict",
        {"name": "n"},
        id="POST /api/entities",
    ),
    pytest.param(
        "POST",
        "/api/relations",
        "command",
        "handle_add_relation",
        "dict",
        {"source_entity_id": str(uuid4()), "target_entity_id": str(uuid4())},
        id="POST /api/relations",
    ),
    pytest.param(
        "DELETE",
        f"/api/documents/{uuid4()}",
        "command",
        "handle_delete_document",
        "dict",
        {},
        id="DELETE /api/documents/{doc_id}",
    ),
    pytest.param(
        "POST",
        "/api/search",
        "query",
        "handle_search_with_meta",
        "dict",
        {"query": "q"},
        id="POST /api/search",
    ),
    pytest.param(
        "POST",
        "/api/search/similar",
        "query",
        "handle_search_similar",
        "dumpable_list",
        {"chunk_id": str(uuid4())},
        id="POST /api/search/similar",
    ),
    pytest.param(
        "POST",
        "/api/search/context",
        "query",
        "handle_search_context",
        "dumpable_list",
        {"query": "q"},
        id="POST /api/search/context",
    ),
    pytest.param(
        "POST",
        "/api/verify",
        "query",
        "handle_verify_answer",
        "dumpable",
        {},
        id="POST /api/verify",
    ),
    pytest.param(
        "POST",
        "/api/query/sql",
        "query",
        "handle_sql_query",
        "list",
        {"sql": "SELECT 1"},
        id="POST /api/query/sql",
    ),
    pytest.param(
        "GET",
        "/api/documents",
        "query",
        "handle_list_documents",
        "dumpable_list",
        {},
        id="GET /api/documents",
    ),
    pytest.param(
        "GET",
        "/api/entities",
        "query",
        "handle_list_entities",
        "dumpable_list",
        {},
        id="GET /api/entities",
    ),
    pytest.param(
        "POST",
        "/api/query",
        "router",
        "handle_routed_query",
        "dumpable",
        {"query": "q"},
        id="POST /api/query",
    ),
    pytest.param(
        "POST",
        "/api/coding/embed-codebook",
        "coding",
        "handle_embed_codebook",
        "dict",
        {},
        id="POST /api/coding/embed-codebook",
    ),
    pytest.param(
        "POST",
        "/api/coding/pool",
        "coding",
        "handle_pool",
        "dict",
        {"codebook_version_id": str(uuid4())},
        id="POST /api/coding/pool",
    ),
    pytest.param(
        "POST",
        "/api/coding/code-batch",
        "coding",
        "handle_code_batch",
        "dict",
        {},
        id="POST /api/coding/code-batch",
    ),
    pytest.param(
        "POST",
        "/api/coding/reliability",
        "coding",
        "handle_reliability",
        "dict",
        {},
        id="POST /api/coding/reliability",
    ),
    pytest.param(
        "POST",
        "/api/coding/calibrate-floors",
        "coding",
        "handle_calibrate_floors",
        "dict",
        {"code_id": "c", "codebook_version_id": str(uuid4())},
        id="POST /api/coding/calibrate-floors",
    ),
    pytest.param(
        "POST",
        "/api/coding/saturation",
        "coding",
        "handle_saturation",
        "dict",
        {},
        id="POST /api/coding/saturation",
    ),
    pytest.param(
        "POST",
        "/api/graph/search",
        "graph",
        "handle_search_graph",
        "list",
        {},
        id="POST /api/graph/search",
    ),
    pytest.param(
        "POST",
        "/api/graph/bfs",
        "graph",
        "handle_bfs",
        "list",
        {"start_entity_id": str(uuid4())},
        id="POST /api/graph/bfs",
    ),
    pytest.param(
        "GET",
        f"/api/graph/relations/{uuid4()}",
        "graph",
        "handle_get_entity_relations",
        "list",
        {},
        id="GET /api/graph/relations/{entity_id}",
    ),
    pytest.param(
        "POST", "/api/tags", "tag", "handle_add_tag", "dict", {"name": "t"}, id="POST /api/tags"
    ),
    pytest.param(
        "POST",
        f"/api/documents/{uuid4()}/tags",
        "tag",
        "handle_tag_document",
        "dict",
        {"tag": "t"},
        id="POST /api/documents/{doc_id}/tags",
    ),
    pytest.param(
        "GET",
        f"/api/documents/{uuid4()}/tags",
        "tag",
        "handle_get_document_tags",
        "list",
        {},
        id="GET /api/documents/{doc_id}/tags",
    ),
    pytest.param(
        "POST",
        "/api/metadata",
        "tag",
        "handle_set_metadata",
        "dict",
        {"key": "k", "value": "v"},
        id="POST /api/metadata",
    ),
    pytest.param(
        "GET", "/api/metadata", "tag", "handle_get_metadata", "list", {}, id="GET /api/metadata"
    ),
    pytest.param(
        "GET",
        "/api/versions",
        "versioning",
        "handle_list_versions",
        "list",
        {},
        id="GET /api/versions",
    ),
    pytest.param(
        "GET", "/api/stats", "versioning", "handle_get_stats", "dict", {}, id="GET /api/stats"
    ),
    pytest.param(
        "GET", "/api/tables", "versioning", "handle_sql_tables", "list", {}, id="GET /api/tables"
    ),
    pytest.param(
        "GET",
        "/api/document-stats",
        "versioning",
        "handle_query_document_stats",
        "dict",
        {},
        id="GET /api/document-stats",
    ),
]


@pytest.mark.parametrize(("method", "path", "seam", "handle", "shape", "body"), _HANDLER_ROUTES)
def test_handler_route_returns_live_json(
    method: str,
    path: str,
    seam: str,
    handle: str,
    shape: str,
    body: dict[str, str],
    monkeypatch: Any,
) -> None:
    seam_def = _SEAMS[seam]
    fake = _FakeHandler(seam_def.real, shape)
    monkeypatch.setattr(seam_def.module, seam_def.factory, lambda: fake)
    if path == "/api/search":
        monkeypatch.setattr(
            query_integrity_module,
            "envelope_to_payload",
            lambda envelope: dict(_CANNED),
        )

    client = TestClient(create_http_app())
    response = client.request(method, path, json=body or None)

    assert _assert_live_response(response) is not None
    assert fake.calls == [handle], f"route reached {fake.calls}, expected [{handle}]"


# ---------------------------------------------------------------------------
# Family C: pool-taking tool routes hosted in api/http.py
# ---------------------------------------------------------------------------


_INGEST_TOOL_ROUTES = [
    pytest.param(
        "GET",
        "/api/ingest/status",
        {},
        ingest_surface_tools,
        "ingest_status",
        id="GET /api/ingest/status",
    ),
    pytest.param(
        "POST",
        "/api/ingest/reembed",
        {},
        ingest_surface_tools,
        "ingest_reembed",
        id="POST /api/ingest/reembed",
    ),
]


@pytest.mark.parametrize(("method", "path", "body", "module", "fn_name"), _INGEST_TOOL_ROUTES)
def test_ingest_tool_route_supplies_pool(
    method: str, path: str, body: dict[str, str], module: Any, fn_name: str, monkeypatch: Any
) -> None:
    real = getattr(module, fn_name)
    captured: dict[str, object] = {}

    async def stub(*args: object, **kwargs: object) -> dict[str, object]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return dict(_CANNED)

    monkeypatch.setattr(module, fn_name, stub)
    pool = _FakePool()
    monkeypatch.setattr(research_http, "_pool", pool)

    client = TestClient(create_http_app())
    response = client.request(method, path, json=body or None)

    assert _assert_live_response(response) == _CANNED
    args = captured.get("args", ())
    assert pool in args, "route did not pass the pool positionally"
    inspect.signature(real).bind(*args, **captured.get("kwargs", {}))


def test_ingest_url_route_supplies_pool(monkeypatch: Any) -> None:
    """POST /api/ingest/url passes (url, pool) positionally plus the kwargs.

    The route imports ``corpus_kb.tools.ingest_tools`` lazily at request
    time; that module pulls the ``web`` package (and ``unstructured``, which
    downloads a spaCy model on import). A fake module in ``sys.modules``
    keeps this smoke test offline while still pinning the route wiring.
    """
    captured: dict[str, object] = {}
    pool = _FakePool()

    async def stub(url: str, sent_pool: Any, **kwargs: object) -> dict[str, object]:
        captured["args"] = (url, sent_pool)
        captured["kwargs"] = kwargs
        return dict(_CANNED)

    fake_module = ModuleType("corpus_kb.tools.ingest_tools")
    fake_module.ingest_url = stub
    monkeypatch.setitem(sys.modules, "corpus_kb.tools.ingest_tools", fake_module)
    monkeypatch.setattr(research_http, "_pool", pool)

    client = TestClient(create_http_app())
    response = client.post("/api/ingest/url", json={"url": "https://example.com"})

    assert _assert_live_response(response) == _CANNED
    assert captured.get("args") == ("https://example.com", pool)
    kwargs = cast("dict[str, object]", captured.get("kwargs", {}))
    assert set(kwargs) <= {"tenant_id", "crawl", "source_type"}
    assert kwargs.get("tenant_id") == DEFAULT_TENANT


def test_ask_route_supplies_pool(monkeypatch: Any) -> None:
    """POST /api/ask reaches the shared ask tool with the shared pool."""
    captured: dict[str, object] = {}

    async def stub(query: str, **kwargs: object) -> dict[str, object]:
        captured["query"] = query
        captured["kwargs"] = kwargs
        return dict(_CANNED)

    monkeypatch.setattr(ask_tools, "ask", stub)
    pool = _FakePool()
    monkeypatch.setattr(research_http, "_pool", pool)

    client = TestClient(create_http_app())
    response = client.post("/api/ask", json={"query": "how does startup work?", "k": 3})

    assert _assert_live_response(response) == _CANNED
    assert captured["query"] == "how does startup work?"
    kwargs = cast("dict[str, object]", captured["kwargs"])
    assert kwargs.get("pg_pool") is pool, "route did not supply the shared pool"
    assert kwargs.get("k") == 3


def test_ask_route_requires_query() -> None:
    """A missing query answers the ONE error envelope, never a 500."""
    client = TestClient(create_http_app())
    response = client.post("/api/ask", json={})
    assert response.status_code == 400
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error_type"] == "validation_error"


def test_embed_route_returns_live_json(monkeypatch: Any) -> None:
    class _FakeEmbedder:
        def embed(self, text: str) -> list[float]:
            return [0.0, 0.0]

    monkeypatch.setattr(coding_handler_module, "load_instruct", lambda: None)
    import corpus_kb.rag.embedder as embedder_module

    monkeypatch.setattr(embedder_module, "OllamaEmbedder", _FakeEmbedder)

    client = TestClient(create_http_app())
    response = client.post("/api/embed", json={"text": "hello"})

    payload = _assert_live_response(response)
    assert payload["dimensions"] == 2
