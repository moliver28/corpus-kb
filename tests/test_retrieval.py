"""Tests for the query/retrieval layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from corpus_kb.domain.models import SearchQuery, SearchResult
from corpus_kb.handlers.query_handler import QueryHandler
from tests.mock_pg import transaction_cm


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
    """Vector and FTS results are fused via the pure-Python rrf_fuse
    (handlers/query_fusion.py — the exact port of corpus.rrf_fusion)."""
    cid = UUID("00000000-0000-0000-0000-000000000001")
    did = UUID("00000000-0000-0000-0000-000000000002")
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=transaction_cm())
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
    # Non-zero: an all-zero query embedding abstains the vector arm (the
    # stage-3 guard), which would skip the vector fetch this test pins.
    embedder.embed.return_value = [0.1] * 2560

    handler = QueryHandler(pool=mock_pool, embedder=embedder)
    results = await handler.handle_search(SearchQuery(query="test", k=1))

    assert len(results) == 1
    assert results[0].chunk_id == cid
    assert results[0].score == pytest.approx(2.0 / 61)


@pytest.mark.asyncio
async def test_rrf_k_config_flows_into_fusion_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retrieval.rrf_k must reach the fusion call — the literal 60 was inert
    (U45 finding: a typed reader existed but the call site never read it).

    sp-query-fusion moved fusion from the corpus.rrf_fusion SQL function to
    the pure-Python rrf_fuse (handlers/query_fusion.py, an exact port). The
    handler binds it at import time, so the recorded call here is the real
    invocation the pipeline makes — config value, legs, and all.
    """
    import corpus_kb.handlers.query_handler as qh

    cid = UUID("00000000-0000-0000-0000-000000000001")
    did = UUID("00000000-0000-0000-0000-000000000002")
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=transaction_cm())
    # Two legs only: the pgml vector arm fetch, then the FTS arm fetch.
    # Fusion itself issues no SQL anymore.
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [{"chunk_id": cid, "text": "vector hit", "doc_id": did, "source": "s1", "score": 0.9}],
            [{"chunk_id": cid, "text": "fts hit", "doc_id": did, "source": "s1", "score": 0.5}],
        ]
    )
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    fusion_calls: list[dict] = []
    real_fuse = qh.rrf_fuse

    def recording_fuse(*args: object, **kwargs: object) -> object:
        fusion_calls.append({"args": args, "kwargs": kwargs})
        return real_fuse(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(qh, "rrf_fuse", recording_fuse)

    embedder = MagicMock()
    embedder.embed.return_value = [0.0] * 768

    handler = QueryHandler(
        pool=mock_pool,
        embedder=embedder,
        config={"embedding": {"provider": "pgml"}, "retrieval": {"rrf_k": 42}},
    )
    results = await handler.handle_search(SearchQuery(query="test", k=1))

    assert len(fusion_calls) == 1
    kwargs = fusion_calls[0]["kwargs"]
    assert kwargs["rrf_k"] == 42  # was hardcoded 60
    assert kwargs["k"] == 1  # query.k unchanged
    # Both legs reached the fusion, dense leg first (the SQL UNION ALL order).
    dense_ids = [str(row["chunk_id"]) for row in kwargs["dense"]]
    lexical_ids = [str(row["chunk_id"]) for row in kwargs["lexical"]]
    assert dense_ids == [str(cid)]
    assert lexical_ids == [str(cid)]
    # The 42 shows up in the math too: the chunk hit both legs at rank 1,
    # so the fused score is 1/43 + 1/43.
    assert len(results) == 1
    assert results[0].score == pytest.approx(2.0 / 43)


def test_search_query_and_result_have_new_fields() -> None:
    from uuid import uuid4

    from corpus_kb.domain.models import SearchQuery, SearchResult

    q = SearchQuery(query="x", self_query=True)
    assert q.self_query is True

    r = SearchResult(
        chunk_id=uuid4(),
        text="t",
        score=0.5,
        source="s",
        doc_id=uuid4(),
        file_path="a.py",
        start_line=1,
        end_line=2,
        chunk_index=0,
        heading_path=["A"],
    )
    assert r.file_path == "a.py"


def test_verify_and_routed_models_import() -> None:
    from corpus_kb.domain.models import (  # noqa: F401  # noqa: F401
        ClaimVerdict,
        RouteDecision,
        RoutedQuery,
        RoutedResult,
        VerifyAnswerQuery,
        VerifyAnswerResult,
    )

    q = VerifyAnswerQuery(answer="x")
    assert q.chunk_ids == []


@pytest.mark.asyncio
async def test_handle_search_returns_provenance_fields() -> None:
    """Mirrors test_hybrid_search_rrf_fusion's mock scaffold, extended with
    the five provenance columns on each mocked row. Constructed with no
    embedder and provider=ollama, so handle_search's vector arm is skipped
    entirely (the `provider == "ollama" and self._embedder` guard) and
    exactly one conn.fetch call happens: the FTS arm. sp-query-fusion moved
    the RRF fusion to the pure-Python rrf_fuse (handlers/query_fusion.py),
    so no fusion SQL is issued. The fusion carries only the five fused
    columns, and provenance is re-attached from the raw FTS rows by
    chunk_id, so the returned SearchResult keeps file_path/start_line/etc."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_transaction = MagicMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=None)
    mock_transaction.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "chunk_id": UUID("00000000-0000-0000-0000-000000000001"),
                "text": "fts hit",
                "doc_id": UUID("00000000-0000-0000-0000-000000000002"),
                "source": "s1",
                "file_path": "a.py",
                "start_line": 10,
                "end_line": 20,
                "chunk_index": 0,
                "heading_path": None,
                "score": 0.9,
            }
        ]
    )
    mock_acquire_cm = MagicMock()
    mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_acquire_cm)

    handler = QueryHandler(
        pool=mock_pool,
        config={
            "embedding": {"provider": "ollama"},  # no embedder -> vector skipped
            # Pin the one-fetch contract: parent expansion would add a second
            # fetch (retrieval.expand_to_parent defaults true in config v2).
            "retrieval": {"expand_to_parent": False},
            "reranking": {"enabled": False},
        },
    )
    results = await handler.handle_search(SearchQuery(query="hello", k=3))
    assert mock_conn.fetch.call_count == 1  # FTS arm only — fusion is Python-side
    assert len(results) == 1
    assert results[0].file_path == "a.py"
    assert results[0].start_line == 10


@pytest.mark.asyncio
async def test_handle_search_filters_tombstoned_and_superseded(pg_pool) -> None:
    """handle_search's dedup predicate must genuinely exclude a tombstoned
    chunk, not pass vacuously because none was ever tombstoned.

    Driving this through a real shrink-and-reingest was structurally
    vacuous: this short markdown text always chunks to exactly 1 chunk
    (heading-aware chunking only splits above config.yaml's
    markdown.max_section_size: 5000), so "shrinking" from 1 chunk to 1
    chunk never tombstones anything -- confirmed empirically via
    chunk_elements() before writing this fix. This test instead sets
    tombstoned_at directly via SQL after a normal ingest, which tests
    exactly what this test file claims to verify: handle_search's
    read-side filter, independent of ingest-side chunking internals
    (covered separately in tests/test_ingest_e2e.py).
    """
    from corpus_kb.domain.models import DEFAULT_TENANT_ID, SearchQuery
    from corpus_kb.handlers.query_handler import QueryHandler
    from corpus_kb.rag.embedder import FakeEmbedder
    from corpus_kb.tools.ingest_common import ingest_text

    config = {
        "graph": {"extract_entities": False},
        "embedding": {"model": "qwen3-embedding:4b", "dimensions": 2560},
    }
    source = "test-search-tombstone-filter"
    unique_phrase = "kerflumph vermillion second section marker"
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pg_pool, DEFAULT_TENANT_ID) as conn:
        await conn.execute(
            "DELETE FROM documents WHERE tenant_id = $1 AND source = $2",
            str(DEFAULT_TENANT_ID),
            source,
        )

    result = await ingest_text(
        text=f"# Doc\n{unique_phrase}.\n",
        pg_pool=pg_pool,
        source_type="markdown",
        config=config,
        source=source,
    )
    assert result["status"] == "success"

    handler = QueryHandler(pg_pool, embedder=FakeEmbedder(config))
    before = await handler.handle_search(SearchQuery(query=unique_phrase, k=10))
    assert any(unique_phrase in r.text for r in before), (
        "sanity check: chunk must be findable before tombstoning"
    )

    # FORCE RLS: the tombstone UPDATE must run with the tenant GUC set, or
    # the policy's current_setting('app.current_tenant_id')::uuid cast fails
    # on the empty unset value.
    async with tenant_connection(pg_pool, DEFAULT_TENANT_ID) as conn:
        updated = await conn.execute(
            "UPDATE chunks SET tombstoned_at = NOW() WHERE tenant_id = $1 AND doc_id = $2",
            str(DEFAULT_TENANT_ID),
            result["document_id"],
        )
    assert updated == "UPDATE 1"

    after = await handler.handle_search(SearchQuery(query=unique_phrase, k=10))
    assert all(unique_phrase not in r.text for r in after)
