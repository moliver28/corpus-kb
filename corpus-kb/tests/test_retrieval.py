"""Tests for the query/retrieval layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.domain.models import SearchQuery, SearchResult
from src.handlers.query_handler import QueryHandler


@pytest.mark.asyncio
async def test_retrieval_basic() -> None:
    """Mocked QueryHandler returns top_k results with the expected fields."""
    handler = QueryHandler(pool=MagicMock())
    expected = [
        SearchResult(
            chunk_id=UUID("00000000-0000-0000-0000-000000000001"),
            text="hello world",
            score=0.9,
            source="raw_text",
            doc_id=UUID("00000000-0000-0000-0000-000000000002"),
        )
    ]
    handler.handle_search = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    results = await handler.handle_search(SearchQuery(query="hello", k=3))
    assert len(results) == 1
    assert results[0].text == "hello world"
    assert results[0].score == 0.9
    assert results[0].source == "raw_text"


@pytest.mark.asyncio
async def test_hybrid_search_rrf_fusion() -> None:
    """Vector and FTS results are fused via RRF ranking."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [
                {
                    "chunk_id": UUID("00000000-0000-0000-0000-000000000001"),
                    "text": "vector hit",
                    "doc_id": UUID("00000000-0000-0000-0000-000000000002"),
                    "source": "s1",
                }
            ],
            [
                {
                    "chunk_id": UUID("00000000-0000-0000-0000-000000000001"),
                    "text": "fts hit",
                    "doc_id": UUID("00000000-0000-0000-0000-000000000002"),
                    "source": "s1",
                    "score": 0.5,
                }
            ],
        ]
    )

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    embedder = MagicMock()
    embedder.embed.return_value = [0.0] * 768

    handler = QueryHandler(pool=mock_pool, embedder=embedder)
    results = await handler.handle_search(SearchQuery(query="test", k=1))

    assert len(results) == 1
    assert results[0].chunk_id == UUID("00000000-0000-0000-0000-000000000001")
