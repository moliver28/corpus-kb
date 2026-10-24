"""Tests for the SQL-native pgml embedding search path.

These tests verify that when ``embedding.provider = "pgml"`` the search query
vector is produced inside PostgreSQL by ``pgml.embed()``, not by a Python
embedder. They use mocked asyncpg pools; live pgml verification is deferred
to the final verification wave (PG16 on localhost lacks pgml).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from corpus_kb.domain.models import SearchQuery
from corpus_kb.handlers.query_handler import QueryHandler
from tests.mock_pg import transaction_cm


def _make_pool(side_effect: list[object]) -> MagicMock:
    """Return a mock asyncpg pool that yields a mock connection."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(side_effect=side_effect)
    mock_conn.transaction = MagicMock(return_value=transaction_cm())

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


def _fused_row(cid: UUID, did: UUID, text: str = "fused") -> dict[str, object]:
    return {
        "chunk_id": cid,
        "text": text,
        "source": "s1",
        "doc_id": did,
        "score": 0.5,
    }


@pytest.mark.asyncio
async def test_pgml_path_uses_inline_embed_and_never_calls_python_embedder() -> None:
    """Provider pgml embeds in SQL; the OllamaEmbedder is not invoked."""
    cid = UUID("00000000-0000-0000-0000-000000000001")
    did = UUID("00000000-0000-0000-0000-000000000002")
    vector_row = {
        "chunk_id": cid,
        "text": "vector hit",
        "doc_id": did,
        "source": "s1",
        "score": 0.9,
    }
    fts_row = {
        "chunk_id": cid,
        "text": "fts hit",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_row = _fused_row(cid, did)

    mock_pool = _make_pool([[vector_row], [fts_row], [fused_row]])

    embedder = MagicMock()
    embedder.embed = MagicMock(side_effect=RuntimeError("embedder must not be called"))

    handler = QueryHandler(
        pool=mock_pool,
        embedder=embedder,
        config={"embedding": {"provider": "pgml", "model": "nomic-embed-text"}},
    )
    query = SearchQuery(query="hello world", k=3)
    results = await handler.handle_search(query)

    embedder.embed.assert_not_called()

    vector_call = mock_pool.acquire.return_value.__aenter__.return_value.fetch.call_args_list[0]
    sql = vector_call.args[0]
    assert "pgml.embed" in sql
    assert "ARRAY[$2]::text[]" in sql
    assert vector_call.args[1] == "nomic-embed-text"
    assert vector_call.args[2] == "hello world"
    assert vector_call.args[3] == str(query.tenant_id)
    # Config v2 live wiring: LIMIT = max(retrieval.vector_candidates, k * 2)
    # = max(150, 6) — the rerank pre-fetch no longer inflates the arm LIMIT.
    assert vector_call.args[4] == 150

    assert len(results) == 1
    assert results[0].chunk_id == cid
    assert results[0].doc_id == did


@pytest.mark.asyncio
async def test_ollama_path_uses_python_embedder() -> None:
    """Provider ollama keeps the existing Python embedder path."""
    cid, did = uuid4(), uuid4()
    vector_row = {
        "chunk_id": cid,
        "text": "vec",
        "doc_id": did,
        "source": "s1",
        "score": 0.9,
    }
    fts_row = {
        "chunk_id": cid,
        "text": "fts",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_row = _fused_row(cid, did)

    mock_pool = _make_pool([[vector_row], [fts_row], [fused_row]])

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 768

    handler = QueryHandler(
        pool=mock_pool,
        embedder=embedder,
        config={"embedding": {"provider": "ollama", "model": "nomic-embed-text"}},
    )
    results = await handler.handle_search(SearchQuery(query="hello", k=2))

    # Two embed calls, same query text: the vector arm and the W6 meta
    # arm both embed "hello". The real OllamaEmbedder serves the second
    # call from its in-memory SHA256 cache (zero extra network).
    assert embedder.embed.call_count == 2
    assert {call.args[0] for call in embedder.embed.call_args_list} == {"hello"}
    vector_call = mock_pool.acquire.return_value.__aenter__.return_value.fetch.call_args_list[0]
    assert "pgml.embed" not in vector_call.args[0]
    assert vector_call.args[1] == str([0.1] * 768)

    assert len(results) == 1


@pytest.mark.asyncio
async def test_pgml_and_ollama_paths_produce_identical_results_on_same_rows() -> None:
    """Both providers see the same mock rows and return equal SearchResults."""
    cid, did = uuid4(), uuid4()
    vector_row = {
        "chunk_id": cid,
        "text": "hit",
        "doc_id": did,
        "source": "s1",
        "score": 0.9,
    }
    fts_row = {
        "chunk_id": cid,
        "text": "hit",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_row = _fused_row(cid, did)

    pgml_pool = _make_pool([[vector_row], [fts_row], [fused_row]])
    ollama_pool = _make_pool([[vector_row], [fts_row], [fused_row]])

    ollama_embedder = MagicMock()
    ollama_embedder.embed.return_value = [0.1] * 768

    pgml_handler = QueryHandler(
        pool=pgml_pool,
        config={"embedding": {"provider": "pgml", "model": "nomic-embed-text"}},
    )
    ollama_handler = QueryHandler(
        pool=ollama_pool,
        embedder=ollama_embedder,
        config={"embedding": {"provider": "ollama", "model": "nomic-embed-text"}},
    )

    query = SearchQuery(query="identical", k=2)
    pgml_results = await pgml_handler.handle_search(query)
    ollama_results = await ollama_handler.handle_search(query)

    assert pgml_results == ollama_results
    assert len(pgml_results) == 1
    assert pgml_results[0].chunk_id == cid


@pytest.mark.asyncio
async def test_pgml_unavailable_degrades_to_fts_only() -> None:
    """When pgml.embed is missing the vector side is skipped with a warning."""
    cid, did = uuid4(), uuid4()
    fts_row = {
        "chunk_id": cid,
        "text": "fts only",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_row = _fused_row(cid, did)

    mock_pool = _make_pool(
        [
            asyncpg.UndefinedFunctionError("function pgml.embed(text, text[]) does not exist"),
            [fts_row],
            [fused_row],
        ]
    )

    handler = QueryHandler(
        pool=mock_pool,
        config={"embedding": {"provider": "pgml", "model": "nomic-embed-text"}},
    )
    results = await handler.handle_search(SearchQuery(query="missing pgml", k=2))

    assert len(results) == 1
    assert results[0].chunk_id == cid


@pytest.mark.asyncio
async def test_pgml_path_handles_empty_query() -> None:
    """Empty query text is forwarded as a bound parameter, not rejected."""
    cid, did = uuid4(), uuid4()
    vector_row = {
        "chunk_id": cid,
        "text": "vec",
        "doc_id": did,
        "source": "s1",
        "score": 0.9,
    }
    fts_row = {
        "chunk_id": cid,
        "text": "fts",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_row = _fused_row(cid, did)

    mock_pool = _make_pool([[vector_row], [fts_row], [fused_row]])

    handler = QueryHandler(
        pool=mock_pool,
        config={"embedding": {"provider": "pgml", "model": "nomic-embed-text"}},
    )
    results = await handler.handle_search(SearchQuery(query="", k=2))

    vector_call = mock_pool.acquire.return_value.__aenter__.return_value.fetch.call_args_list[0]
    assert vector_call.args[2] == ""
    assert len(results) == 1


@pytest.mark.asyncio
async def test_unknown_provider_raises_clear_error() -> None:
    """A misspelled or unsupported provider is rejected before any SQL runs."""
    mock_pool = _make_pool([])
    handler = QueryHandler(
        pool=mock_pool,
        config={"embedding": {"provider": "typo-provider", "model": "x"}},
    )
    with pytest.raises(ValueError, match="typo-provider"):
        await handler.handle_search(SearchQuery(query="x", k=2))


@pytest.mark.asyncio
async def test_default_config_takes_ollama_path() -> None:
    """With no explicit config, QueryHandler loads the shipped defaults.

    Config v2 flips embedding.provider to ollama. Without an embedder wired
    the vector arm records REASON_NO_EMBEDDER and degrades to lexical-only
    (that honesty is asserted below) — no pgml.embed SQL is issued."""
    cid, did = uuid4(), uuid4()
    vector_row = {
        "chunk_id": cid,
        "text": "vec",
        "doc_id": did,
        "source": "s1",
        "score": 0.9,
    }
    fts_row = {
        "chunk_id": cid,
        "text": "fts",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_row = _fused_row(cid, did)

    mock_pool = _make_pool([[vector_row], [fts_row], [fused_row]])

    handler = QueryHandler(pool=mock_pool)
    results = await handler.handle_search(SearchQuery(query="defaults", k=2))

    fetches = mock_pool.acquire.return_value.__aenter__.return_value.fetch.call_args_list
    assert all("pgml.embed" not in call.args[0] for call in fetches)
    # The abstaining vector arm issues no fetch; the FTS arm is the first of
    # two (the second is the parent-expansion read, fail-open on the mock).
    # Fusion is the pure Python rrf_fuse — no fusion SQL round trip remains.
    assert len(fetches) == 2
    assert "plainto_tsquery" in fetches[0].args[0]
    assert all("corpus.rrf_fusion" not in call.args[0] for call in fetches)
    assert len(results) == 1
