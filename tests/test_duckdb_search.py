"""DuckDB search backend: hybrid query/ask legs on the mirror schema (§13 sp-duckdb-search).

HERMETIC: a tmp .duckdb file with the full migration mirror; embeddings come
from a deterministic hash stub, so no Ollama and no Postgres are needed.

Pins:
* the DuckDbSearchBackend serves the vector (exact-scan) and lexical (fts
  macro) arms on the mirror schema, with the honesty envelope's guaranteed
  keys and DuckDB engine modes;
* the FTS index lifecycle: lazy count-watermark rebuild (the DuckDB fts
  index never auto-updates, W0 spike 6);
* zero-vector candidate rows sort deterministically LAST (explicit
  sentinel; PG keeps them unreachable via the degraded filter);
* query + ask legs through the EXISTING QueryHandler seam (the handler
  dispatches on a DuckDbGateway pool) with SEARCH_ENVELOPE byte-parity;
* determinism: same query twice -> identical result order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from corpus_kb.ask.composer import compose_answer
from corpus_kb.contracts.envelopes import SEARCH_ENVELOPE
from corpus_kb.domain.models import SearchQuery, SearchSimilarQuery
from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_integrity import envelope_to_payload
from corpus_kb.storage.duckdb_search import (
    DUCKDB_LEXICAL_INDEX_MODE,
    DUCKDB_VECTOR_INDEX_MODE,
    DuckDbSearchBackend,
)
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal
from corpus_kb.storage.gateway import DuckDbGateway
from corpus_kb.storage.search_backend import SearchOutcome, SearchRequest

pytest.importorskip("duckdb")

TENANT1 = "a0000000-0000-0000-0000-000000000001"
TENANT2 = "b0000000-0000-0000-0000-000000000002"
DOC1 = "d0000000-0000-0000-0000-000000000001"
DOC2 = "d0000000-0000-0000-0000-000000000002"
C_BOTH = "c0000000-0000-0000-0000-000000000001"  # hits both arms for "quick"
C_LEX = "c0000000-0000-0000-0000-000000000002"  # lexical only (alien embedding)
C_ZERO = "c0000000-0000-0000-0000-000000000003"  # zero-vector candidate
C_DEAD = "c0000000-0000-0000-0000-000000000004"  # tombstoned: invisible
C_T2 = "c0000000-0000-0000-0000-000000000005"  # other tenant: invisible

_HANDLER_CONFIG: dict[str, Any] = {
    "embedding": {"provider": "ollama"},
    "retrieval": {"exclude_degraded": True},
    "search": {"reranker": "none"},
    "reranking": {"enabled": False},
}


def _bucket(token: str, dims: int) -> int:
    """Deterministic, collision-free-for-this-corpus token bucket."""
    return sum(ord(ch) for ch in token) % dims


def _one_hot(token: str, dims: int = 2560) -> list[float]:
    vec = [0.0] * dims
    vec[_bucket(token, dims)] = 1.0
    return vec


class StubEmbedder:
    """Deterministic hash bag-of-words embedder (hermetic Ollama stand-in)."""

    def __init__(self, dims: int = 2560, zero: bool = False) -> None:
        self.dims = dims
        self.zero = zero

    def embed(self, text: str) -> list[float]:
        if self.zero:
            return [0.0] * self.dims
        vec = [0.0] * self.dims
        for token in text.lower().split():
            vec[_bucket(token, self.dims)] += 1.0
        return vec


class StubLlm:
    """The ask composer's canned chat stub (test_ask_composer shape)."""

    model = "stub-model"

    async def chat(
        self, messages: list[dict[str, str]], model: str | None = None, options: object = None
    ) -> dict[str, object]:
        return {"message": {"content": "Quick is the indexed term [1]."}}


async def _seed(gateway: DuckDbGateway) -> None:
    """The shared corpus: one doc per tenant, five chunks (see id comments)."""
    for tenant, doc in ((TENANT1, DOC1), (TENANT2, DOC2)):
        await gateway.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type, file_hash)"
            " VALUES (?, ?, ?, ?, ?)",
            doc,
            tenant,
            "seed.md",
            "text",
            f"hash-{tenant}",
        )
    rows: list[tuple[str, str, str, list[float], bool]] = [
        (C_BOTH, TENANT1, "quick", _one_hot("quick"), False),
        (C_LEX, TENANT1, "quick walrus", _one_hot("zzalien"), False),
        (C_ZERO, TENANT1, "quick sloth", [0.0] * 2560, False),
        (C_DEAD, TENANT1, "quick ghost", _one_hot("quick"), True),
        (C_T2, TENANT2, "quick foreigner", _one_hot("quick"), False),
    ]

    for i, (cid, tenant, text, emb, degraded) in enumerate(rows):
        doc = DOC1 if tenant == TENANT1 else DOC2
        await gateway.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)"
            " VALUES (?, ?, ?, ?, ?)",
            cid,
            tenant,
            doc,
            i,
            text,
        )
        # The varchar CAST bind is ~10 ms; a bound ARRAY param is ~2.7 s
        # per 2560-float statement on DuckDB 1.4.5 (vector_literal note).
        await gateway.execute(
            "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding_model, embedding,"
            " degraded) VALUES (?, ?, ?, CAST(? AS FLOAT[2560]), ?)",
            cid,
            tenant,
            "stub",
            vector_literal(emb),
            degraded,
        )
    await gateway.execute("UPDATE chunks SET tombstoned_at = now() WHERE chunk_id = ?", C_DEAD)


@pytest.fixture
async def search_gw(tmp_path: Path):
    """Migrated + seeded DuckDB gateway on a fresh tmp file."""
    from corpus_kb._setup.migrate import run_duckdb_migrations

    db = tmp_path / "search.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(db))
    gateway = await DuckDbGateway.connect(str(db))
    await _seed(gateway)
    try:
        yield gateway
    finally:
        await gateway.close()


def _request(gateway: DuckDbGateway, semantic: str, k: int = 10) -> SearchRequest:
    return SearchRequest(
        conn=cast("Any", gateway),
        query=SearchQuery(query=semantic, k=k, tenant_id=UUID(TENANT1)),
        semantic_query=semantic,
        dynamic_predicates=[],
        degraded_pred="AND cv.degraded = FALSE",
        over_retrieve_n=0,
        reasons=[],
    )


def _handler(gateway: DuckDbGateway, embedder: StubEmbedder | None = None) -> QueryHandler:
    return QueryHandler(
        gateway,
        embedder=embedder if embedder is not None else StubEmbedder(),
        config=dict(_HANDLER_CONFIG),
    )


# ---------------------------------------------------------------------------
# Parity pins: the mirrored reason strings and arm fragments
# ---------------------------------------------------------------------------


def test_reason_and_fragment_literals_match_the_pg_arm_definitions() -> None:
    # storage must not import handlers, so the DuckDB backend mirrors the
    # literals; this pin makes any drift on either side CI-loud.
    from corpus_kb.handlers import query_integrity, query_vector_arms
    from corpus_kb.storage import duckdb_search

    assert duckdb_search.REASON_ZERO_QUERY == query_integrity.REASON_ZERO_QUERY
    assert duckdb_search.REASON_VECTOR_ARM_ERROR == query_integrity.REASON_VECTOR_ARM_ERROR
    assert duckdb_search.REASON_NO_EMBEDDER == query_integrity.REASON_NO_EMBEDDER
    assert duckdb_search.REASON_FILTER_EMPTY == query_integrity.REASON_FILTER_EMPTY
    assert duckdb_search.SELECT_LIST == query_vector_arms.SELECT_LIST
    assert duckdb_search.DEDUP_PREDICATE == query_vector_arms.DEDUP_PREDICATE


# ---------------------------------------------------------------------------
# FTS index lifecycle (the index never auto-updates: W0 spike 6)
# ---------------------------------------------------------------------------


async def test_fts_ensure_creates_once_then_skips(tmp_path: Path) -> None:
    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.storage.duckdb_fts import ensure_fts_index

    db = tmp_path / "fts.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(db))
    gateway = await DuckDbGateway.connect(str(db))
    try:
        assert await ensure_fts_index(gateway) is True  # missing -> created
        assert await ensure_fts_index(gateway) is False  # fresh -> no rebuild
    finally:
        await gateway.close()


async def test_fts_ensure_rebuilds_on_drift_and_search_sees_new_rows(
    search_gw: DuckDbGateway,
) -> None:
    from corpus_kb.storage.duckdb_fts import ensure_fts_index

    assert await ensure_fts_index(search_gw) is True
    await search_gw.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)"
        " VALUES ('c0000000-0000-0000-0000-000000000009', ?, ?, 9, 'zzneedle token')",
        TENANT1,
        DOC1,
    )
    assert await ensure_fts_index(search_gw) is True  # count watermark drifted
    backend = DuckDbSearchBackend(embedder=StubEmbedder(), config=dict(_HANDLER_CONFIG))
    outcome = await backend.search(_request(search_gw, "zzneedle"))
    assert [str(r["chunk_id"]) for r in outcome.lexical_rows] == [
        "c0000000-0000-0000-0000-000000000009"
    ]


# ---------------------------------------------------------------------------
# The backend arms on the mirror schema
# ---------------------------------------------------------------------------


async def test_hybrid_fusion_orders_both_arms_and_hides_dead_foreign_rows(
    search_gw: DuckDbGateway,
) -> None:
    handler = _handler(search_gw)
    results = await handler.handle_search(SearchQuery(query="quick", k=10, tenant_id=UUID(TENANT1)))
    ids = [str(r.chunk_id) for r in results]
    assert ids == [C_BOTH, C_LEX, C_ZERO]
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True) and len(set(scores)) == 3
    assert C_DEAD not in ids and C_T2 not in ids


async def test_zero_vector_candidate_sorts_last_in_the_dense_arm(
    search_gw: DuckDbGateway,
) -> None:
    backend = DuckDbSearchBackend(embedder=StubEmbedder(), config=dict(_HANDLER_CONFIG))
    outcome = await backend.search(_request(search_gw, "quick"))
    dense_ids = [str(r["chunk_id"]) for r in outcome.vector_rows]
    assert dense_ids[-1] == C_ZERO
    assert dense_ids[0] == C_BOTH
    assert outcome.lexical_index_mode == DUCKDB_LEXICAL_INDEX_MODE
    assert outcome.vector_index_mode == DUCKDB_VECTOR_INDEX_MODE


async def test_tricky_lexical_queries_never_raise(search_gw: DuckDbGateway) -> None:
    backend = DuckDbSearchBackend(embedder=StubEmbedder(), config=dict(_HANDLER_CONFIG))
    for tricky in ['unclosed "quote (parens', "* wildcard", "知识库", "it's the"]:
        outcome = await backend.search(_request(search_gw, tricky))
        assert isinstance(outcome, SearchOutcome)


async def test_stopword_only_query_skips_the_lexical_arm(search_gw: DuckDbGateway) -> None:
    backend = DuckDbSearchBackend(embedder=StubEmbedder(), config=dict(_HANDLER_CONFIG))
    request = _request(search_gw, "the of and")
    outcome = await backend.search(request)
    assert outcome.lexical_rows == []  # sanitized empty: arm never binds
    assert [str(r["chunk_id"]) for r in outcome.vector_rows] == [C_BOTH, C_LEX, C_ZERO]


async def test_zero_query_embedding_abstains_dense_but_lexical_runs(
    search_gw: DuckDbGateway,
) -> None:
    backend = DuckDbSearchBackend(embedder=StubEmbedder(zero=True), config=dict(_HANDLER_CONFIG))
    reasons: list[str] = []
    request = _request(search_gw, "quick")
    request = SearchRequest(
        conn=request.conn,
        query=request.query,
        semantic_query=request.semantic_query,
        dynamic_predicates=request.dynamic_predicates,
        degraded_pred=request.degraded_pred,
        over_retrieve_n=request.over_retrieve_n,
        reasons=reasons,
    )
    outcome = await backend.search(request)
    assert outcome.vector_rows == []
    assert len(outcome.lexical_rows) == 3
    assert reasons == ["zero-query-embedding"]


async def test_determinism_same_query_twice_identical_order(
    search_gw: DuckDbGateway,
) -> None:
    handler = _handler(search_gw)
    query = SearchQuery(query="quick", k=10, tenant_id=UUID(TENANT1))
    first = [(str(r.chunk_id), r.score) for r in await handler.handle_search(query)]
    second = [(str(r.chunk_id), r.score) for r in await handler.handle_search(query)]
    assert first == second


# ---------------------------------------------------------------------------
# search_similar: full parity with the pgvector arm (handlers/query_crud.py)
# ---------------------------------------------------------------------------


def _similar_query(chunk_id: str, k: int = 10) -> SearchSimilarQuery:
    return SearchSimilarQuery(chunk_id=UUID(chunk_id), tenant_id=UUID(TENANT1), k=k)


async def test_search_similar_orders_like_the_pg_contract(search_gw: DuckDbGateway) -> None:
    handler = _handler(search_gw)
    results = await handler.handle_search_similar(_similar_query(C_BOTH))
    ids = [str(r.chunk_id) for r in results]
    # Nearest first; the zero-vector candidate LAST (sentinel, pgvector
    # would order it by NaN); tombstoned + foreign-tenant rows invisible.
    assert ids == [C_LEX, C_ZERO]
    assert results[0].score > results[1].score
    top = results[0]
    assert (top.text, top.source, top.doc_id) == ("quick walrus", "seed.md", UUID(DOC1))
    assert top.heading_path is None and top.file_path is None


async def test_search_similar_zero_seed_stays_deterministic(
    search_gw: DuckDbGateway,
) -> None:
    handler = _handler(search_gw)
    results = await handler.handle_search_similar(_similar_query(C_ZERO))
    # Every distance is 2.0 (DuckDB is finite, pgvector NaN): the chunk_id
    # tiebreaker must pin the order so replays cannot permute rows.
    ids = [str(r.chunk_id) for r in results]
    assert ids == sorted([C_BOTH, C_LEX])
    assert all(r.score == -1.0 for r in results)


async def test_search_similar_seed_without_embedding_returns_empty(
    search_gw: DuckDbGateway,
) -> None:
    await search_gw.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)"
        " VALUES ('c0000000-0000-0000-0000-000000000008', ?, ?, 8, 'no vector yet')",
        TENANT1,
        DOC1,
    )
    handler = _handler(search_gw)
    no_vec = "c0000000-0000-0000-0000-000000000008"
    results = await handler.handle_search_similar(_similar_query(no_vec))
    assert results == []


def test_vector_literal_is_the_canonical_9g_form() -> None:
    # The one formatter (storage.duckdb_support): compact, no spaces,
    # float32-faithful at nine significant digits — where repr(float)
    # would print sixteen and drift from the stored FLOAT[k] value.
    from corpus_kb.storage.duckdb_support import vector_literal as canonical

    assert vector_literal is canonical
    assert vector_literal([0.1, 1 / 3, 1e-8]) == "[0.1,0.333333333,1e-08]"
    assert " " not in vector_literal([0.25, -2.5, 3.0])
    assert vector_literal([]) == "[]"


# ---------------------------------------------------------------------------
# Handler seam: envelope parity + the ask leg
# ---------------------------------------------------------------------------


async def test_search_envelope_keys_match_the_contract_exactly(
    search_gw: DuckDbGateway,
) -> None:
    handler = _handler(search_gw)
    payload = envelope_to_payload(
        await handler.handle_search_with_meta(
            SearchQuery(query="quick", k=10, tenant_id=UUID(TENANT1))
        )
    )
    assert list(payload) == list(SEARCH_ENVELOPE)
    assert payload["vector_index_mode"] == DUCKDB_VECTOR_INDEX_MODE
    assert payload["lexical_index_mode"] == DUCKDB_LEXICAL_INDEX_MODE
    assert "vector_index_note" not in payload and "lexical_index_note" not in payload


async def test_degraded_empty_envelope_carries_the_remedy(search_gw: DuckDbGateway) -> None:
    handler = _handler(search_gw, embedder=StubEmbedder(zero=True))
    payload = envelope_to_payload(
        await handler.handle_search_with_meta(
            SearchQuery(query="qqnohit", k=10, tenant_id=UUID(TENANT1))
        )
    )
    assert payload["status"] == "degraded_empty"
    assert list(payload) == [*list(SEARCH_ENVELOPE), "remedy"]
    assert payload["degraded_excluded"]["reasons"] == ["zero-query-embedding"]


async def test_ask_leg_composes_a_grounded_answer_on_duckdb(
    search_gw: DuckDbGateway,
) -> None:
    payload = (
        await compose_answer(
            "what is quick?",
            _handler(search_gw),
            StubLlm(),
            tenant_id=UUID(TENANT1),
            k=5,
        )
    ).to_payload()
    assert payload["status"] == "success"
    assert payload["result"], "ask must carry retrieved evidence"


# ---------------------------------------------------------------------------
# Error-injection pins for the three sentinel swallows (issue 235)
#
# Each swallow answers an empty sentinel on ANY failure, so a test that
# only asserts the sentinel stays green even when the SQL breaks. Each
# test first runs the healthy path (non-empty or counted), then injects
# the documented failure class — a real driver CatalogException from a
# dropped table — and pins the sentinel. The vector arm also pins the
# reason string that names the driver class, so the sentinel is provably
# the swallow's answer, not a skip branch's.
# ---------------------------------------------------------------------------


async def test_meta_arm_missing_table_answers_empty(search_gw: DuckDbGateway) -> None:
    backend = DuckDbSearchBackend(embedder=StubEmbedder(), config=dict(_HANDLER_CONFIG))
    await search_gw.execute(
        "INSERT INTO meta_embeddings (tenant_id, meta_kind, meta_key, label, source_count,"
        " embedding) VALUES (?, 'topic', 'quick', 'Quick', 1, CAST(? AS FLOAT[2560]))",
        TENANT1,
        vector_literal(_one_hot("quick")),
    )
    request = _request(search_gw, "quick")
    healthy = await backend._meta_rows(request, search_gw, TENANT1)
    assert [row["meta_key"] for row in healthy] == ["quick"]  # the arm really reads
    await search_gw.execute("DROP TABLE meta_embeddings")
    assert await backend._meta_rows(request, search_gw, TENANT1) == []


async def test_vector_arm_missing_table_degrades_with_the_driver_class(
    search_gw: DuckDbGateway,
) -> None:
    backend = DuckDbSearchBackend(embedder=StubEmbedder(), config=dict(_HANDLER_CONFIG))
    request = _request(search_gw, "quick")
    healthy, healthy_reason = await backend._vector_rows(request, search_gw, TENANT1, 10)
    assert healthy and healthy_reason is None  # the arm really reads
    await search_gw.execute("DROP TABLE chunks_vectors")
    rows, reason = await backend._vector_rows(request, search_gw, TENANT1, 10)
    assert rows == []
    assert reason == "vector-arm-error (CatalogException)"


async def test_degraded_probe_missing_table_counts_zero(search_gw: DuckDbGateway) -> None:
    backend = DuckDbSearchBackend(embedder=StubEmbedder(), config=dict(_HANDLER_CONFIG))
    request = _request(search_gw, "quick")
    healthy = await backend._degraded_probe(search_gw, request, TENANT1, [])
    assert healthy == 3  # live candidates only: the tombstoned row stays invisible
    await search_gw.execute("DROP TABLE chunks_vectors")
    assert await backend._degraded_probe(search_gw, request, TENANT1, []) == 0
