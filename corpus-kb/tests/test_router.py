"""Tests for RouterHandler's embedding-similarity query classifier."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.models import RoutedQuery
from src.handlers.router_handler import RouterHandler
from src.rag.embedder import FakeEmbedder

_CONFIG = {
    "embedding": {"dimensions": 4096},
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


class _ZeroVectorEmbedder:
    """Sync embedder that always returns an all-zero (degraded) vector."""

    def embed(self, text: str) -> list[float]:
        return [0.0] * 4096


@pytest.mark.asyncio
async def test_zero_vector_query_falls_back_to_semantic() -> None:
    query_handler = MagicMock()
    query_handler.handle_search = AsyncMock(return_value=[])
    router = RouterHandler(
        query_handler, MagicMock(), MagicMock(), _ZeroVectorEmbedder(), _CONFIG
    )

    result = await router.handle_routed_query(RoutedQuery(query="anything"))
    assert result.decision.fallback is True
    assert result.decision.route == "semantic"
    query_handler.handle_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_relational_query_routes_to_stats() -> None:
    query_handler = MagicMock()
    versioning_handler = MagicMock()
    versioning_handler.handle_query_document_stats = AsyncMock(
        return_value={"total_docs": 3}
    )
    embedder = FakeEmbedder(_CONFIG)
    router = RouterHandler(
        query_handler, MagicMock(), versioning_handler, embedder, _CONFIG
    )

    result = await router.handle_routed_query(
        RoutedQuery(query="how many documents mention X")
    )
    assert result.decision.route == "relational"
    versioning_handler.handle_query_document_stats.assert_awaited_once()
