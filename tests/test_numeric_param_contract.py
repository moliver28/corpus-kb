"""Per-surface pins for the numeric-param contract (issue #238).

One failure class everywhere: a bad numeric param (k, limit, and kin)
raises the taxonomy ValidationError — HTTP 400 with the parity error
envelope, socket -32602 (migrated from -32603, approved contract
change). One defaults table: absent params reach the shared layer with
the catalogue default, and valid values pass through untouched (no
clamp, no silent fallback).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

import corpus_kb.api.research_http as research_http_module
import corpus_kb.handlers.graph_handler as graph_handler_module
import corpus_kb.handlers.query_handler as query_handler_module
import corpus_kb.handlers.query_integrity as query_integrity_module
import corpus_kb.handlers.router_handler as router_handler_module
import corpus_kb.tools.cluster_tools as cluster_tools_module
import corpus_kb.tools.statements_tools as statements_tools_module
import corpus_kb.tools.vector_screen_tools as vector_screen_tools_module
from corpus_kb.api.http import create_http_app
from corpus_kb.api.socket import JSONRPCServer
from corpus_kb.domain.models import RouteDecision, RoutedResult


class _FakeQueryHandler:
    """Records query-model kwargs; answers the plain success envelope."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    async def handle_search_with_meta(self, query: Any) -> dict[str, Any]:
        self.seen.append(query)
        return {"status": "success", "k": query.k}

    async def handle_search_similar(self, query: Any) -> list[dict[str, Any]]:
        self.seen.append(query)
        return []

    async def handle_search_context(self, query: Any) -> list[dict[str, Any]]:
        self.seen.append(query)
        return []

    async def handle_list_documents(self, query: Any) -> list[dict[str, Any]]:
        self.seen.append(query)
        return []


class _FakeRouterHandler:
    def __init__(self) -> None:
        self.seen: list[Any] = []

    async def handle_routed_query(self, query: Any) -> Any:
        self.seen.append(query)
        return RoutedResult(
            decision=RouteDecision(route="semantic", confidence=0.9, fallback=False),
            results=[],
        )


class _FakeGraphHandler:
    def __init__(self) -> None:
        self.seen: list[Any] = []

    async def handle_get_entity_relations(self, *args: Any, **kwargs: Any) -> list[Any]:
        self.seen.append(kwargs.get("limit"))
        return []


def _install_http(monkeypatch: Any) -> tuple[TestClient, _FakeQueryHandler]:
    query_handler = _FakeQueryHandler()
    monkeypatch.setattr(query_handler_module, "get_query_handler", lambda: query_handler)
    monkeypatch.setattr(router_handler_module, "get_router_handler", lambda: _FakeRouterHandler())
    # The param contract is what this file pins; the real envelope
    # serialization is the flagship gate's job (test_surface_parity_deep).
    monkeypatch.setattr(
        query_integrity_module,
        "envelope_to_payload",
        lambda envelope: {"status": "success", "result": [], "degraded_excluded": {}},
    )
    return TestClient(create_http_app()), query_handler


async def _fake_pool() -> object:
    return object()


@pytest.fixture(autouse=True)
def _fake_research_pool(monkeypatch: Any) -> None:
    monkeypatch.setattr(research_http_module, "research_pool", _fake_pool)


# ---------------------------------------------------------------------------
# HTTP: the search family (pass-through None-contract + pinned defaults)
# ---------------------------------------------------------------------------


def test_http_search_junk_k_is_validation_error(monkeypatch: Any) -> None:
    client, _ = _install_http(monkeypatch)

    response = client.post("/api/search", json={"query": "q", "k": "abc"})

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error"
    assert body["error_type"] == "validation_error"
    assert "k" in body["error"]


def test_http_search_k_passes_through(monkeypatch: Any) -> None:
    client, handler = _install_http(monkeypatch)

    assert client.post("/api/search", json={"query": "q", "k": 7}).status_code == 200
    assert handler.seen[-1].k == 7


def test_http_search_absent_k_stays_none(monkeypatch: Any) -> None:
    """The None-contract: an omitted k rides reranking.return_top_k."""
    client, handler = _install_http(monkeypatch)

    assert client.post("/api/search", json={"query": "q"}).status_code == 200
    assert handler.seen[-1].k is None


def test_http_search_zero_k_is_not_clamped(monkeypatch: Any) -> None:
    client, handler = _install_http(monkeypatch)

    assert client.post("/api/search", json={"query": "q", "k": 0}).status_code == 200
    assert handler.seen[-1].k == 0


def test_http_search_negative_k_is_validation_error(monkeypatch: Any) -> None:
    """DELIBERATE (#238 follow-up): a negative k used to ride to Postgres,
    whose negative-LIMIT rejection answered 500/-32603. The parse helper
    now refuses it with the caller's failure class."""

    response = _install_http(monkeypatch)[0].post("/api/search", json={"query": "q", "k": -5})

    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"
    assert "k" in response.json()["error"]


def test_http_search_huge_k_is_validation_error(monkeypatch: Any) -> None:
    """Past the Postgres int8 bind the database answered -32603; refuse up front."""

    response = _install_http(monkeypatch)[0].post(
        "/api/search", json={"query": "q", "k": 9223372036854775808}
    )

    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_http_search_similar_defaults_and_junk(monkeypatch: Any) -> None:
    client, handler = _install_http(monkeypatch)
    similar = {"chunk_id": "11111111-1111-1111-1111-111111111111"}

    assert client.post("/api/search/similar", json=similar).status_code == 200
    assert handler.seen[-1].k == 10

    response = client.post("/api/search/similar", json={**similar, "k": "abc"})
    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_http_search_context_defaults_and_junk(monkeypatch: Any) -> None:
    client, handler = _install_http(monkeypatch)

    assert client.post("/api/search/context", json={"query": "q"}).status_code == 200
    assert handler.seen[-1].k == 5
    assert handler.seen[-1].context_chunks == 2

    response = client.post("/api/search/context", json={"query": "q", "context_chunks": "abc"})
    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_http_query_junk_k_is_validation_error(monkeypatch: Any) -> None:
    client, _ = _install_http(monkeypatch)

    response = client.post("/api/query", json={"query": "q", "k": "abc"})

    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


# ---------------------------------------------------------------------------
# HTTP: the list families (defaults from the catalogue, junk refuses)
# ---------------------------------------------------------------------------


def test_http_statements_list_junk_limit_is_validation_error(monkeypatch: Any) -> None:
    """Migrated: this limit used to fall back to 50 silently."""

    async def fake_list(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success", "statements": [], "count": 0}

    monkeypatch.setattr(statements_tools_module, "statements_list", fake_list)
    client, _ = _install_http(monkeypatch)

    response = client.post("/api/statements/list", json={"limit": "junk"})

    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_http_statements_list_absent_limit_is_catalogue_default(monkeypatch: Any) -> None:
    seen: list[Any] = []

    async def fake_list(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("limit"))
        return {"status": "success", "statements": [], "count": 0}

    monkeypatch.setattr(statements_tools_module, "statements_list", fake_list)
    client, _ = _install_http(monkeypatch)

    assert client.post("/api/statements/list", json={}).status_code == 200
    assert seen == [50]


def test_http_outliers_rank_junk_k_is_validation_error(monkeypatch: Any) -> None:
    """Migrated failure class: this used to be an unmapped ValueError 400."""

    async def fake_rank(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success"}

    monkeypatch.setattr(vector_screen_tools_module, "outliers_rank", fake_rank)
    client, _ = _install_http(monkeypatch)

    response = client.post("/api/analysis/outliers-rank", json={"k": "abc"})

    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_http_extraction_tune_junk_limit_is_validation_error(monkeypatch: Any) -> None:
    """Migrated: this limit used to fall back to 8 silently."""

    async def fake_tune(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success"}

    import corpus_kb.tools.extraction_tune_tools as tune_module

    monkeypatch.setattr(tune_module, "extraction_tune", fake_tune)
    client, _ = _install_http(monkeypatch)

    assert client.post("/api/extraction-tune", json={"limit": "junk"}).status_code == 400
    response = client.post("/api/extraction-tune", json={"min_confidence": "high"})
    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_http_extraction_tune_non_finite_is_validation_error(monkeypatch: Any) -> None:
    """NaN/Infinity poison every downstream comparison; the JSON RFC bans
    the bare literals but the server's parser accepts them, so the wire
    shapes below are pinned: digit strings, overflow strings, and the
    bare NaN/Infinity literals."""

    async def fake_tune(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success"}

    import corpus_kb.tools.extraction_tune_tools as tune_module

    monkeypatch.setattr(tune_module, "extraction_tune", fake_tune)
    client, _ = _install_http(monkeypatch)

    for bad in ("nan", "inf", "-inf", "1e999"):
        response = client.post("/api/extraction-tune", json={"min_confidence": bad})
        assert response.status_code == 400, bad
        assert response.json()["error_type"] == "validation_error", bad
    for literal in ("NaN", "Infinity", "-Infinity"):
        raw = '{"min_confidence": ' + literal + "}"
        response = client.post(
            "/api/extraction-tune", content=raw, headers={"content-type": "application/json"}
        )
        assert response.status_code == 400, literal
        assert response.json()["error_type"] == "validation_error", literal


def test_http_labeling_triage_junk_is_validation_error(monkeypatch: Any) -> None:
    """The #261 labeling family joins the strict class (was bare casts)."""

    async def fake_triage(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success"}

    import corpus_kb.tools.labeling_triage_tools as triage_module

    monkeypatch.setattr(triage_module, "labeling_triage", fake_triage)
    client, _ = _install_http(monkeypatch)

    for body in ({"cv_folds": "abc"}, {"flag_threshold": "abc"}, {"limit": -1}):
        response = client.post("/api/labeling/triage", json=body)
        assert response.status_code == 400, body
        assert response.json()["error_type"] == "validation_error", body


def test_http_labeling_triage_defaults_and_pass_through(monkeypatch: Any) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_triage(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(dict(kwargs))
        return {"status": "success"}

    import corpus_kb.tools.labeling_triage_tools as triage_module

    monkeypatch.setattr(triage_module, "labeling_triage", fake_triage)
    client, _ = _install_http(monkeypatch)

    assert client.post("/api/labeling/triage", json={}).status_code == 200
    assert (
        client.post(
            "/api/labeling/triage", json={"cv_folds": 7, "flag_threshold": 0.3, "limit": 5}
        ).status_code
        == 200
    )
    assert seen[0]["cv_folds"] == 5
    assert seen[0]["flag_threshold"] == 0.2
    assert seen[0]["limit"] == 50
    assert (seen[1]["cv_folds"], seen[1]["flag_threshold"], seen[1]["limit"]) == (7, 0.3, 5)


def test_http_labeling_gold_check_junk_is_validation_error(monkeypatch: Any) -> None:
    async def fake_gold(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success"}

    import corpus_kb.tools.labeling_gold_tools as gold_module

    monkeypatch.setattr(gold_module, "labeling_gold_check", fake_gold)
    client, _ = _install_http(monkeypatch)

    response = client.post(
        "/api/labeling/gold-check", json={"gold_file": "g.json", "confidence_level": "abc"}
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_http_coding_reliability_negative_seed_passes(monkeypatch: Any) -> None:
    """Seeds are any-int (allow_negative): a negative seed reaches the handler."""

    class _FakeCodingHandler:
        async def handle_reliability(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            seen.append(kwargs.get("seed"))
            return {"status": "success"}

    seen: list[Any] = []
    import corpus_kb.handlers.coding_handler as coding_handler_module

    monkeypatch.setattr(coding_handler_module, "get_coding_handler", lambda: _FakeCodingHandler())
    client, _ = _install_http(monkeypatch)

    response = client.post("/api/coding/reliability", json={"seed": -5})
    assert response.status_code == 200
    assert seen == [-5]


def test_http_get_documents_junk_limit_is_validation_error(monkeypatch: Any) -> None:
    client, _ = _install_http(monkeypatch)

    response = client.get("/api/documents", params={"limit": "abc"})

    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


def test_http_graph_relations_junk_limit_is_validation_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(graph_handler_module, "get_graph_handler", lambda: _FakeGraphHandler())
    client, _ = _install_http(monkeypatch)
    entity_id = "55555555-5555-5555-5555-555555555555"

    response = client.get(f"/api/graph/relations/{entity_id}", params={"limit": "abc"})

    assert response.status_code == 400
    assert response.json()["error_type"] == "validation_error"


# ---------------------------------------------------------------------------
# Socket: junk answers -32602 (migrated from -32603 / silent default)
# ---------------------------------------------------------------------------


async def _call(server: JSONRPCServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return await server._process_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    )


async def test_socket_search_junk_k_is_32602(monkeypatch: Any) -> None:
    """Migrated: a junk k used to surface as -32603 (internal error)."""
    _install_http(monkeypatch)

    response = await _call(JSONRPCServer(), "search", {"query": "q", "k": "abc"})

    assert response["error"]["code"] == -32602
    assert "k" in response["error"]["message"]


async def test_socket_statements_list_junk_limit_is_32602(monkeypatch: Any) -> None:
    """Migrated: this limit used to fall back to 50 silently."""

    async def fake_list(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success", "statements": [], "count": 0}

    monkeypatch.setattr(statements_tools_module, "statements_list", fake_list)

    response = await _call(JSONRPCServer(), "statements_list", {"limit": "junk"})

    assert response["error"]["code"] == -32602


async def test_socket_clusters_inspect_junk_is_32602(monkeypatch: Any) -> None:
    """Migrated: this junk value used to surface as -32603 (ValueError)."""

    async def fake_inspect(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success"}

    monkeypatch.setattr(cluster_tools_module, "clusters_inspect", fake_inspect)

    response = await _call(JSONRPCServer(), "clusters_inspect", {"min_cluster_size": "abc"})

    assert response["error"]["code"] == -32602


async def test_socket_search_negative_k_is_32602(monkeypatch: Any) -> None:
    """DELIBERATE: a negative k used to ride to Postgres and surface as
    -32603 (negative-LIMIT bind rejection); the parse helper refuses it."""

    _install_http(monkeypatch)

    response = await _call(JSONRPCServer(), "search", {"query": "q", "k": -5})

    assert response["error"]["code"] == -32602
    assert "k" in response["error"]["message"]


async def test_socket_labeling_triage_junk_is_32602(monkeypatch: Any) -> None:
    """The #261 labeling arms join the strict class (were bare casts -> -32603)."""

    async def fake_triage(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success"}

    import corpus_kb.tools.labeling_triage_tools as triage_module

    monkeypatch.setattr(triage_module, "labeling_triage", fake_triage)

    server = JSONRPCServer()
    for params in ({"cv_folds": "abc"}, {"flag_threshold": "abc"}, {"limit": -2}):
        response = await _call(server, "labeling_triage", params)
        assert response["error"]["code"] == -32602, params


async def test_socket_labeling_triage_defaults_pass(monkeypatch: Any) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_triage(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(dict(kwargs))
        return {"status": "success"}

    import corpus_kb.tools.labeling_triage_tools as triage_module

    monkeypatch.setattr(triage_module, "labeling_triage", fake_triage)

    assert (await _call(JSONRPCServer(), "labeling_triage", {}))["result"]["status"] == "success"
    assert (seen[0]["cv_folds"], seen[0]["flag_threshold"], seen[0]["limit"]) == (5, 0.2, 50)


async def test_socket_labeling_gold_check_junk_is_32602(monkeypatch: Any) -> None:
    async def fake_gold(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success"}

    import corpus_kb.tools.labeling_gold_tools as gold_module

    monkeypatch.setattr(gold_module, "labeling_gold_check", fake_gold)

    response = await _call(
        JSONRPCServer(), "labeling_gold_check", {"gold_file": "g.json", "confidence_level": "nan"}
    )

    assert response["error"]["code"] == -32602


async def test_socket_statements_list_limit_passes_through(monkeypatch: Any) -> None:
    seen: list[Any] = []

    async def fake_list(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("limit"))
        return {"status": "success", "statements": [], "count": 0}

    monkeypatch.setattr(statements_tools_module, "statements_list", fake_list)
    server = JSONRPCServer()

    assert (await _call(server, "statements_list", {"limit": 5}))["result"]["status"] == "success"
    assert (await _call(server, "statements_list", {}))["result"]["status"] == "success"
    assert seen == [5, 50]


async def test_socket_search_absent_k_stays_none(monkeypatch: Any) -> None:
    handler = _install_http(monkeypatch)[1]

    assert (await _call(JSONRPCServer(), "search", {"query": "q"}))["result"]["status"] == (
        "success"
    )
    assert handler.seen[-1].k is None
