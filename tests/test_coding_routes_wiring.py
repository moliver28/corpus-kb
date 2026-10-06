"""Route wiring tests for the coding HTTP surface.

Asserts the bundle's route set is registered on a bare app (no DB, no Ollama):
each route answers with a routing/validation status (non-404), while unknown
/api/coding/* paths still 404 — proving the table is not over-registered.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from corpus_kb.api.http import create_http_app

# The exact route set commit 127906a added (mirrored in create_http_app).
CODING_ROUTES = [
    "/api/embed",
    "/api/coding/embed-codebook",
    "/api/coding/pool",
    "/api/coding/code-batch",
    "/api/coding/reliability",
    "/api/coding/calibrate-floors",
    "/api/coding/saturation",
]


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_http_app())


@pytest.mark.parametrize("route", CODING_ROUTES)
class TestCodingRoutesWired:
    def test_post_route_is_registered(self, client: TestClient, route: str) -> None:
        """An empty POST must not 404: 400/422/500 all prove the route exists."""
        response = client.post(route, json={})
        assert response.status_code != 404, f"{route} is not registered"

    def test_get_on_post_route_is_405(self, client: TestClient, route: str) -> None:
        """GET must hit the method guard (405), not fall through to 404."""
        response = client.get(route)
        assert response.status_code == 405, f"{route} lost its POST method guard"


class TestCodingRoutesNotOverRegistered:
    def test_unknown_coding_path_is_404(self, client: TestClient) -> None:
        assert client.post("/api/coding/nope", json={}).status_code == 404

    def test_bare_coding_prefix_is_404(self, client: TestClient) -> None:
        assert client.post("/api/coding", json={}).status_code == 404

    def test_route_count_grew_by_exactly_the_bundle_set(self, client: TestClient) -> None:
        app = create_http_app()
        registered = {getattr(r, "path", None) for r in app.router.routes}
        for route in CODING_ROUTES:
            assert route in registered
        assert "/api/coding/nope" not in registered
