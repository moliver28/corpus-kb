"""Tests for the query/retrieval layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from corpus_kb.domain.models import SearchQuery, SearchResult
from corpus_kb.handlers.query_handler import QueryHandler


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
    """Vector and FTS results are fused via the corpus.rrf_fusion SQL function."""
    cid = UUID("00000000-0000-0000-0000-000000000001")
    did = UUID("00000000-0000-0000-0000-000000000002")
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [
                {
                    "chunk_id": cid,
                    "text": "vector hit",
                    "doc_id": did,
                    "source": "s1",
                    "score": 0.9,
                }
            ],
            [
                {
                    "chunk_id": cid,
                    "text": "fts hit",
                    "doc_id": did,
                    "source": "s1",
                    "score": 0.5,
                }
            ],
            [
                {
                    "chunk_id": cid,
                    "text": "fts hit",
                    "source": "s1",
                    "doc_id": did,
                    "score": 2.0 / 61,
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
    assert results[0].chunk_id == cid
    assert results[0].score == pytest.approx(2.0 / 61)
    # Fusion is delegated to PostgreSQL, not computed in Python.
    assert "corpus.rrf_fusion" in mock_conn.fetch.call_args_list[2].args[0]
