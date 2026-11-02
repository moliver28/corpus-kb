"""Ollama vector arm for QueryHandler (local embeddings, optional matryoshka).

The arm embeds the query with the local ``OllamaEmbedder`` and fetches
candidate rows with the halfvec scan, or the matryoshka two-tier fetch
when it is on. ``rows`` is the candidate list the RRF fusion consumes;
``reason`` is ``None`` on a clean run or the honesty string for the
search envelope's ``degraded_excluded``. The arm never raises for a
failed fetch — the savepoint wrapper degrades and says so.

Stage 3 zero-query guard: an all-zero query embedding makes every
cosine distance NaN (0/0), which sorts FIRST under DESC — the row
helpers abstain instead of returning garbage confident-looking hits.
The lexical arm still runs.

The score ORDER BY clauses stay INLINE here on purpose: the determinism
audit counts them in this module's source
(``tests/test_query_determinism.py``).
"""

from __future__ import annotations

import asyncpg

from corpus_kb.handlers.query_integrity import REASON_ZERO_QUERY, is_all_zero
from corpus_kb.handlers.query_vector_rows import (
    _CANDIDATE_SPINE,
    _CTE_SPINE,
    DEDUP_PREDICATE,
    SELECT_LIST,
    _candidate_limit,
    _run_in_savepoint,
    _use_exact_scan,
)
from corpus_kb.rag.embedder import OllamaEmbedder
from corpus_kb.rag.self_query import Predicate


async def _matryoshka_rows(
    conn: asyncpg.Connection,
    embedder: OllamaEmbedder,
    semantic_query: str,
    tenant_id: str,
    vector_limit: int,
    candidate_n: int,
    dynamic_predicates: list[Predicate],
    degraded_pred: str,
    matryoshka_dim: int,
) -> tuple[list[asyncpg.Record], str | None]:
    """The matryoshka two-tier fetch: sliced-dim probe, exact re-order.

    Stage 3 zero-query guard: an all-zero query embedding makes every
    cosine distance NaN (0/0), which sorts FIRST under DESC — abstain
    instead of returning garbage confident-looking hits. The lexical arm
    still runs.
    """
    from corpus_kb.rag.self_query import build_filter_sql

    qfull = embedder.embed(semantic_query)
    frag, params = build_filter_sql(dynamic_predicates, start_index=6)
    if is_all_zero(qfull):
        return [], REASON_ZERO_QUERY
    qslice = embedder.embed_matryoshka(semantic_query, matryoshka_dim)
    # U40: MATERIALIZED candidate CTE (sliced-dim probe scan) + exact
    # full-dimension re-ordering OUTSIDE the CTE.
    vector_results = await conn.fetch(
        f"""
        WITH cand AS MATERIALIZED (
            SELECT cv.chunk_id, cv.embedding
            {_CTE_SPINE}
            WHERE cv.tenant_id = $2 AND cv.vector_1024 IS NOT NULL
              {DEDUP_PREDICATE} {frag} {degraded_pred}
            ORDER BY cv.vector_1024 <=> $4::vector
            LIMIT $5
        )
        SELECT {SELECT_LIST},
               1 - (cand.embedding <=> $1::halfvec) AS score
        FROM cand
        JOIN chunks ON cand.chunk_id = chunks.chunk_id
        JOIN documents ON chunks.doc_id = documents.doc_id
        ORDER BY (1 - (cand.embedding <=> $1::halfvec)) + 0 DESC,
                 chunks.chunk_id ASC
        LIMIT $3
        """,
        str(qfull),
        tenant_id,
        vector_limit,
        str(qslice),
        candidate_n,
        *params,
    )
    return vector_results, None


async def _halfvec_rows(
    conn: asyncpg.Connection,
    query_vector: list[float],
    tenant_id: str,
    vector_limit: int,
    dynamic_predicates: list[Predicate],
    degraded_pred: str,
    exact_threshold: float,
) -> tuple[list[asyncpg.Record], str | None]:
    """The halfvec arm fetch: selectivity probe, exact or relaxed ANN scan."""
    from corpus_kb.rag.self_query import build_filter_sql

    frag, params = build_filter_sql(dynamic_predicates, start_index=4)
    # Stage 3 zero-query guard (see the matryoshka arm).
    if is_all_zero(query_vector):
        return [], REASON_ZERO_QUERY
    # U21: same selectivity probe + exact-scan fallback as the pgml arm
    # (identical result schema and params).
    use_exact = await _use_exact_scan(
        conn, dynamic_predicates, degraded_pred, tenant_id, exact_threshold
    )
    if use_exact:
        # No distance ORDER BY/LIMIT inside the CTE: the planner cannot
        # use the ordered HNSW scan here, so every matching row is
        # scored exactly and the outer ORDER BY picks the true top-k.
        vector_results = await conn.fetch(
            f"""
            WITH candidate AS MATERIALIZED (
                SELECT cv.chunk_id AS candidate_chunk_id,
                       1 - (cv.embedding <=> $1::halfvec) AS score
                {_CTE_SPINE}
                WHERE cv.tenant_id = $2 {DEDUP_PREDICATE} {frag} {degraded_pred}
            )
            {_CANDIDATE_SPINE}
            ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
            LIMIT $3
            """,
            str(query_vector),
            tenant_id,
            vector_limit,
            *params,
        )
    else:
        # U40 relaxed-ordering pattern: scan inside a MATERIALIZED CTE,
        # exact ordering outside (`score + 0`).
        vector_results = await conn.fetch(
            f"""
            WITH candidate AS MATERIALIZED (
                SELECT cv.chunk_id AS candidate_chunk_id,
                       1 - (cv.embedding <=> $1::halfvec) AS score
                {_CTE_SPINE}
                WHERE cv.tenant_id = $2 {DEDUP_PREDICATE} {frag} {degraded_pred}
                ORDER BY cv.embedding <=> $1::halfvec
                LIMIT $3
            )
            {_CANDIDATE_SPINE}
            ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
            LIMIT $3
            """,
            str(query_vector),
            tenant_id,
            vector_limit,
            *params,
        )
    return vector_results, None


async def ollama_vector_arm(
    conn: asyncpg.Connection,
    *,
    embedder: OllamaEmbedder,
    semantic_query: str,
    tenant_id: str,
    k: int,
    vector_candidates: int,
    dynamic_predicates: list[Predicate],
    degraded_pred: str,
    matryoshka_enabled: bool,
    matryoshka_dim: int,
    candidate_multiplier: int,
    exact_threshold: float,
) -> tuple[list[asyncpg.Record], str | None]:
    """Local Ollama vector arm (optional matryoshka two-tier retrieval)."""

    async def fetch() -> tuple[list[asyncpg.Record], str | None]:
        vector_limit = _candidate_limit(k, vector_candidates)
        if matryoshka_enabled:
            candidate_n = k * int(candidate_multiplier)
            return await _matryoshka_rows(
                conn,
                embedder,
                semantic_query,
                tenant_id,
                vector_limit,
                candidate_n,
                dynamic_predicates,
                degraded_pred,
                matryoshka_dim,
            )
        query_vector = embedder.embed(semantic_query)
        return await _halfvec_rows(
            conn,
            query_vector,
            tenant_id,
            vector_limit,
            dynamic_predicates,
            degraded_pred,
            exact_threshold,
        )

    return await _run_in_savepoint(conn, fetch)
