"""Tests for the reranker abstraction selected by ``search.reranker``.

IdentityReranker (``"none"``, the default) passes fused results through
unchanged. PgmlReranker (``"pgml"``) calls ``pgml.rank()`` inside PostgreSQL
and reorders the fused top-k by cross-encoder score, degrading to the
original order with a warning when pgml is unavailable.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from corpus_kb.domain.models import SearchQuery, SearchResult
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.rag.reranker import (
    FakeReranker,
    IdentityReranker,
    PgmlReranker,
    Reranker,
    build_reranker,
    create_reranker,
)
from tests.mock_pg import transaction_cm

PGML_CONFIG = {
    "search": {
        "reranker": "pgml",
        "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    }
}


def _result(text: str, score: float = 0.5) -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        text=text,
        score=score,
        source="s1",
        doc_id=uuid4(),
    )


def _make_pool(
    fetch_return: list[object] | None = None,
    fetch_side_effect: list[object] | None = None,
) -> MagicMock:
    """Return a mock asyncpg pool yielding one mock connection."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=transaction_cm())
    if fetch_side_effect is not None:
        mock_conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_conn.fetch = AsyncMock(return_value=fetch_return or [])

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


def _last_conn(mock_pool: MagicMock) -> AsyncMock:
    return mock_pool.acquire.return_value.__aenter__.return_value


# ---------------------------------------------------------------------------
# create_reranker factory
# ---------------------------------------------------------------------------


def test_create_reranker_defaults_to_identity_when_search_key_missing() -> None:
    """Given a config without a search section, the factory returns identity."""
    assert isinstance(create_reranker(config={}), IdentityReranker)


def test_create_reranker_none_returns_identity() -> None:
    """Given search.reranker="none", the factory returns IdentityReranker."""
    reranker = create_reranker(config={"search": {"reranker": "none"}})
    assert isinstance(reranker, IdentityReranker)


def test_create_reranker_pgml_returns_pgml_with_default_model() -> None:
    """Given search.reranker="pgml", the factory returns a configured PgmlReranker."""
    reranker = create_reranker(config={"search": {"reranker": "pgml"}})
    assert isinstance(reranker, PgmlReranker)
    assert reranker.model == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_create_reranker_unknown_value_raises() -> None:
    """Given an unknown search.reranker value, the factory raises ValueError."""
    with pytest.raises(ValueError, match="typo-reranker"):
        create_reranker(config={"search": {"reranker": "typo-reranker"}})


def test_rerankers_satisfy_protocol() -> None:
    """Both concrete rerankers satisfy the Reranker protocol."""
    assert isinstance(IdentityReranker(), Reranker)
    assert isinstance(PgmlReranker(config={"search": {}}), Reranker)


# ---------------------------------------------------------------------------
# IdentityReranker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identity_reranker_passes_through_preserving_order() -> None:
    """Identity returns the same list object — no copy, no reorder, no cache."""
    results = [_result("a", 0.9), _result("b", 0.1), _result("c", 0.5)]
    out = await IdentityReranker().rerank("query", results)
    assert out is results
    assert [r.text for r in out] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_identity_reranker_empty_input() -> None:
    """Identity on an empty list returns an empty list."""
    assert await IdentityReranker().rerank("query", []) == []


# ---------------------------------------------------------------------------
# PgmlReranker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pgml_reranker_reorders_by_cross_encoder_score() -> None:
    """pgml.rank scores reorder results; original RRF scores are preserved."""
    results = [_result("first", 0.9), _result("second", 0.8), _result("third", 0.7)]
    # pgml.rank emits rows in RANK order (highest score first), keyed by
    # corpus_id — not in input order. The reranker must map by corpus_id.
    pool = _make_pool(
        fetch_return=[
            {"corpus_id": 1, "score": 0.9},
            {"corpus_id": 2, "score": 0.5},
            {"corpus_id": 0, "score": 0.1},
        ]
    )
    reranker = PgmlReranker(config=PGML_CONFIG, pool=pool)

    out = await reranker.rerank("query text", results)

    fetch_call = _last_conn(pool).fetch.call_args_list[0]
    assert "pgml.rank" in fetch_call.args[0]
    assert fetch_call.args[1] == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert fetch_call.args[2] == "query text"
    assert fetch_call.args[3] == ["first", "second", "third"]

    assert [r.text for r in out] == ["second", "third", "first"]
    assert [r.score for r in out] == [0.8, 0.7, 0.9]


@pytest.mark.asyncio
async def test_pgml_reranker_unavailable_warns_and_returns_unreranked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When pgml.rank is missing, log a warning and keep the fused order."""
    results = [_result("a", 0.9), _result("b", 0.8)]
    pool = _make_pool(
        fetch_side_effect=asyncpg.UndefinedFunctionError(
            "function pgml.rank(text, text, text[]) does not exist"
        )
    )
    reranker = PgmlReranker(config=PGML_CONFIG, pool=pool)

    with caplog.at_level(logging.WARNING):
        out = await reranker.rerank("q", results)

    assert out == results
    assert "pgml.rank" in caplog.text


@pytest.mark.asyncio
async def test_pgml_reranker_without_pool_returns_unreranked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A PgmlReranker with no pool degrades to identity with a warning."""
    results = [_result("a", 0.9), _result("b", 0.8)]
    reranker = PgmlReranker(config=PGML_CONFIG, pool=None)

    with caplog.at_level(logging.WARNING):
        out = await reranker.rerank("q", results)

    assert out == results
    assert "no pool" in caplog.text


@pytest.mark.asyncio
async def test_pgml_reranker_empty_input_issues_no_sql() -> None:
    """Empty fused input short-circuits before any SQL is issued."""
    pool = _make_pool()
    reranker = PgmlReranker(config=PGML_CONFIG, pool=pool)

    out = await reranker.rerank("q", [])

    assert out == []
    _last_conn(pool).fetch.assert_not_called()


@pytest.mark.asyncio
async def test_pgml_reranker_empty_query_is_bound_parameter() -> None:
    """An empty query string is forwarded as a bound parameter, not rejected."""
    results = [_result("a"), _result("b")]
    pool = _make_pool(fetch_return=[{"corpus_id": 1, "score": 0.8}, {"corpus_id": 0, "score": 0.2}])
    reranker = PgmlReranker(config=PGML_CONFIG, pool=pool)

    out = await reranker.rerank("", results)

    fetch_call = _last_conn(pool).fetch.call_args_list[0]
    assert fetch_call.args[2] == ""
    assert [r.text for r in out] == ["b", "a"]


@pytest.mark.asyncio
async def test_pgml_reranker_score_count_mismatch_returns_unreranked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A score/result count mismatch is distrusted: keep the fused order."""
    results = [_result("a"), _result("b"), _result("c")]
    pool = _make_pool(fetch_return=[{"corpus_id": 0, "score": 0.5}])
    reranker = PgmlReranker(config=PGML_CONFIG, pool=pool)

    with caplog.at_level(logging.WARNING):
        out = await reranker.rerank("q", results)

    assert out == results


# ---------------------------------------------------------------------------
# QueryHandler wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_search_pgml_reranker_reorders_fused_results() -> None:
    """search.reranker="pgml": handle_search reranks via pgml.rank after fusion."""
    cid1, cid2, did = uuid4(), uuid4(), uuid4()
    fts_row = {
        "chunk_id": cid1,
        "text": "alpha",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_rows = [
        {
            "chunk_id": cid1,
            "text": "alpha",
            "source": "s1",
            "doc_id": did,
            "score": 0.9,
        },
        {"chunk_id": cid2, "text": "beta", "source": "s1", "doc_id": did, "score": 0.8},
    ]
    rank_rows = [{"corpus_id": 1, "score": 0.9}, {"corpus_id": 0, "score": 0.1}]
    # provider=ollama without embedder -> vector side empty; fetch order:
    # fts, fusion, rank.
    pool = _make_pool(fetch_side_effect=[[fts_row], fused_rows, rank_rows])

    handler = QueryHandler(
        pool=pool,
        config={"embedding": {"provider": "ollama"}, **PGML_CONFIG},
    )
    results = await handler.handle_search(SearchQuery(query="q", k=2))

    assert [r.chunk_id for r in results] == [cid2, cid1]
    assert [r.score for r in results] == [0.8, 0.9]

    rank_call = _last_conn(pool).fetch.call_args_list[2]
    assert "pgml.rank" in rank_call.args[0]
    assert rank_call.args[1] == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert rank_call.args[2] == "q"
    assert rank_call.args[3] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_handle_search_default_reranker_issues_no_rank_sql() -> None:
    """Default search.reranker="none": fused order returned, no pgml.rank call."""
    cid1, cid2, did = uuid4(), uuid4(), uuid4()
    fts_row = {
        "chunk_id": cid1,
        "text": "alpha",
        "doc_id": did,
        "source": "s1",
        "score": 0.5,
    }
    fused_rows = [
        {
            "chunk_id": cid1,
            "text": "alpha",
            "source": "s1",
            "doc_id": did,
            "score": 0.9,
        },
        {"chunk_id": cid2, "text": "beta", "source": "s1", "doc_id": did, "score": 0.8},
    ]
    pool = _make_pool(fetch_side_effect=[[fts_row], fused_rows])

    handler = QueryHandler(
        pool=pool,
        config={"embedding": {"provider": "ollama"}},
    )
    results = await handler.handle_search(SearchQuery(query="q", k=2))

    assert [r.chunk_id for r in results] == [cid1, cid2]
    assert _last_conn(pool).fetch.call_count == 2


# ---------------------------------------------------------------------------
# FakeReranker / build_reranker (score-based pipeline rerankers)
# ---------------------------------------------------------------------------


def test_fake_reranker_is_deterministic() -> None:
    r = FakeReranker()
    first = r.score("q", ["a", "b"])
    second = r.score("q", ["a", "b"])
    assert first == second
    assert len(first) == 2


def test_build_reranker_returns_none_when_disabled() -> None:
    cfg = {"search": {"rerank": {"enabled": False}}}
    assert build_reranker(cfg) is None


def test_build_reranker_returns_fake_in_test_mode() -> None:
    cfg = {"search": {"rerank": {"enabled": True, "backend": "fake"}}}
    reranker = build_reranker(cfg)
    assert reranker is not None
    assert reranker.score("q", ["x"]) == FakeReranker().score("q", ["x"])
