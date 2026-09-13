"""Regression tests for adaptive routing on the unified-postgres stack.

Covers the three scenarios from the adaptive-routing fix:
  (a) sync embedder path (FakeEmbedder) classifies and routes
      semantic / relational / graph.
  (b) async embedder path (async def embed, like PgmlEmbedder) returns
      vectors without coroutine leakage.
  (c) zero-vector (degraded) embedder falls back to semantic search and
      calls handle_search.

The query text matches a prototype phrase exactly, so the deterministic
FakeEmbedder/async stub vector for the query is identical to its centroid
and classification is unambiguous (cosine similarity 1.0).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.models import RouteDecision, RoutedQuery
from src.handlers.router_handler import RouterHandler
from src.rag.embedder import FakeEmbedder

_CONFIG = {
    "embedding": {"dimensions": 768},
    "routing": {
        "enabled": True,
        "confidence_threshold": 0.35,
        "prototypes": {
            "semantic": ["explain the concept of X"],
            "relational": ["how many documents mention X"],
            "graph": ["what is related to X"],
        },
    },
}


class _AsyncEmbedder:
    """Stub embedder whose embed is a coroutine, mirroring PgmlEmbedder."""

    def __init__(self, dimensions: int = 768) -> None:
        self._inner = FakeEmbedder({"embedding": {"dimensions": dimensions}})

    async def embed(self, text: str) -> list[float]:
        return self._inner.embed(text)


class _ZeroVectorEmbedder:
    """Sync embedder that always returns an all-zero (degraded) vector."""

    def __init__(self, dimensions: int = 768) -> None:
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        return [0.0] * self._dimensions


def _router(
    embedder,
    query_handler=None,
    graph_handler=None,
    versioning_handler=None,
) -> RouterHandler:
    return RouterHandler(
        query_handler or MagicMock(),
        graph_handler or MagicMock(),
        versioning_handler or MagicMock(),
        embedder,
        _CONFIG,
    )


# ============================================================================
# Scenario (a): sync FakeEmbedder path classifies and routes all three routes
# ============================================================================


@pytest.mark.asyncio
async def test_sync_embedder_routes_semantic() -> None:
    query_handler = MagicMock()
    query_handler.handle_search = AsyncMock(return_value=[])
    router = _router(FakeEmbedder(_CONFIG), query_handler=query_handler)

    result = await router.handle_routed_query(
        RoutedQuery(query="explain the concept of X")
    )

    assert result.decision.route == "semantic"
    assert result.decision.fallback is False
    query_handler.handle_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_embedder_routes_relational() -> None:
    versioning_handler = MagicMock()
    versioning_handler.handle_query_document_stats = AsyncMock(
        return_value={"total_documents": 3}
    )
    router = _router(FakeEmbedder(_CONFIG), versioning_handler=versioning_handler)

    result = await router.handle_routed_query(
        RoutedQuery(query="how many documents mention X")
    )

    assert result.decision.route == "relational"
    assert result.decision.fallback is False
    versioning_handler.handle_query_document_stats.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_embedder_routes_graph() -> None:
    graph_handler = MagicMock()
    graph_handler.handle_search_graph = AsyncMock(return_value=[{"entity_id": "ent-1"}])
    graph_handler.handle_get_entity_relations = AsyncMock(
        return_value=[{"relation_id": "rel-1"}]
    )
    router = _router(FakeEmbedder(_CONFIG), graph_handler=graph_handler)

    result = await router.handle_routed_query(RoutedQuery(query="what is related to X"))

    assert result.decision.route == "graph"
    assert result.decision.fallback is False
    graph_handler.handle_search_graph.assert_awaited_once()
    graph_handler.handle_get_entity_relations.assert_awaited_once()


# ============================================================================
# Scenario (b): async embedder path returns vectors without coroutine leakage
# ============================================================================


@pytest.mark.asyncio
async def test_async_embedder_returns_vectors_without_coroutine_leakage() -> None:
    query_handler = MagicMock()
    query_handler.handle_search = AsyncMock(return_value=[])
    router = _router(_AsyncEmbedder(), query_handler=query_handler)

    result = await router.handle_routed_query(
        RoutedQuery(query="explain the concept of X")
    )

    assert isinstance(result.decision, RouteDecision)
    assert not inspect.isawaitable(result.decision)
    assert result.decision.route == "semantic"
    assert isinstance(result.decision.confidence, float)
    query_handler.handle_search.assert_awaited_once()


# ============================================================================
# Scenario (c): zero-vector embedder falls back to semantic and calls search
# ============================================================================


@pytest.mark.asyncio
async def test_zero_vector_embedder_falls_back_to_semantic() -> None:
    query_handler = MagicMock()
    query_handler.handle_search = AsyncMock(return_value=[])
    router = _router(_ZeroVectorEmbedder(), query_handler=query_handler)

    result = await router.handle_routed_query(RoutedQuery(query="anything"))

    assert result.decision.route == "semantic"
    assert result.decision.confidence == 0.0
    assert result.decision.fallback is True
    query_handler.handle_search.assert_awaited_once()
