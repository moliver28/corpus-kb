"""The HTTP composition root returns FastAPI (UX program Wave 3).

The swap keeps every existing starlette.routing.Route, the TestClient
and uvicorn unchanged (FastAPI subclasses Starlette). These tests pin
the instance type, the /api route surface (subset), one live GET with a
stubbed handler seam, and the additive default routes.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.routing import Route
from starlette.testclient import TestClient

from corpus_kb.api.http import create_http_app


def test_create_http_app_returns_fastapi() -> None:
    app = create_http_app()
    assert isinstance(app, FastAPI)


def test_existing_api_routes_still_present() -> None:
    app = create_http_app()
    paths = {getattr(route, "path", "") for route in app.routes if isinstance(route, Route)}
    for expected in (
        "/api/ask",
        "/api/documents",
        "/api/search",
        "/api/ingest/file",
        "/api/topics",
        "/api/coding/pool",
    ):
        assert expected in paths


def test_starlette_route_methods_survive_the_swap() -> None:
    app = create_http_app()
    methods: dict[str, set[str]] = {}
    for route in app.routes:
        if isinstance(route, Route):
            methods[route.path] = set(route.methods or set())
    assert "POST" in methods["/api/ask"]
    assert "GET" in methods["/api/documents"]
    assert "DELETE" in methods["/api/documents/{doc_id}"]


def test_testclient_get_documents_with_stubbed_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One live route through the TestClient over a stubbed seam."""
    import corpus_kb.handlers.query_handler as query_handler_module

    class _FakeHandler:
        async def handle_list_documents(self, query: object) -> list[object]:
            return []

    monkeypatch.setattr(
        query_handler_module,
        "get_query_handler",
        lambda: _FakeHandler(),
    )
    client = TestClient(create_http_app())
    response = client.get("/api/documents")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "result": []}


def test_fastapi_default_routes_are_additive() -> None:
    """FastAPI adds /docs + /openapi.json; nothing else moved."""
    client = TestClient(create_http_app())
    assert client.get("/openapi.json").status_code == 200


def test_unknown_route_answers_fastapi_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 stays parseable JSON (FastAPI's detail envelope)."""
    client = TestClient(create_http_app())
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
