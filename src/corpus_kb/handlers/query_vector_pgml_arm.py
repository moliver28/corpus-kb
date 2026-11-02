"""PGML vector arm for QueryHandler (in-database pgml.embed retrieval).

The arm embeds the query with ``pgml.embed`` inside the database and
fetches candidate rows with an exact or relaxed ANN scan. ``rows`` is
the candidate list the RRF fusion consumes. The arm never raises for a
failed fetch — the savepoint wrapper degrades and says so.

The score ORDER BY clauses stay INLINE here on purpose: the determinism
audit counts them in this module's source
(``tests/test_query_determinism.py``).
"""

from __future__ import annotations

import asyncpg

from corpus_kb.handlers.query_vector_rows import (
    _CANDIDATE_SPINE,
    _CTE_SPINE,
    DEDUP_PREDICATE,
    _candidate_limit,
    _run_in_savepoint,
    _use_exact_scan,
)
from corpus_kb.rag.self_query import Predicate


async def _pgml_exact_rows(
    conn: asyncpg.Connection,
    model: str,
    semantic_query: str,
    tenant_id: str,
    vector_limit: int,
    frag: str,
    degraded_pred: str,
    params: list[object],
) -> list[asyncpg.Record]:
    """U40 exact scan: MATERIALIZED candidates, exact ordering OUTSIDE."""
    return await conn.fetch(
        f"""
        WITH candidate AS MATERIALIZED (
            SELECT cv.chunk_id AS candidate_chunk_id,
                   1 - (cv.embedding <=> (
                       SELECT * FROM pgml.embed($1, ARRAY[$2]::text[]) LIMIT 1
                   )::halfvec) AS score
            {_CTE_SPINE}
            WHERE cv.tenant_id = $3 {DEDUP_PREDICATE} {frag} {degraded_pred}
        )
        {_CANDIDATE_SPINE}
        ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
        LIMIT $4
        """,
        model,
        semantic_query,
        tenant_id,
        vector_limit,
        *params,
    )


async def _pgml_relaxed_rows(
    conn: asyncpg.Connection,
    model: str,
    semantic_query: str,
    tenant_id: str,
    vector_limit: int,
    frag: str,
    degraded_pred: str,
    params: list[object],
) -> list[asyncpg.Record]:
    """U40 relaxed scan: ANN order + LIMIT INSIDE the CTE (pgvector >= 0.8).

    The exact ordering is applied OUTSIDE over the retrieved set
    (``score + 0`` defeats planner sort-elision, per the pgvector docs).
    """
    return await conn.fetch(
        f"""
        WITH candidate AS MATERIALIZED (
            SELECT cv.chunk_id AS candidate_chunk_id,
                   1 - (cv.embedding <=> (
                       SELECT * FROM pgml.embed($1, ARRAY[$2]::text[]) LIMIT 1
                   )::halfvec) AS score
            {_CTE_SPINE}
            WHERE cv.tenant_id = $3 {DEDUP_PREDICATE} {frag} {degraded_pred}
            ORDER BY cv.embedding <=> (
                SELECT * FROM pgml.embed($1, ARRAY[$2]::text[]) LIMIT 1
            )::halfvec
            LIMIT $4
        )
        {_CANDIDATE_SPINE}
        ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
        LIMIT $4
        """,
        model,
        semantic_query,
        tenant_id,
        vector_limit,
        *params,
    )


async def pgml_vector_arm(
    conn: asyncpg.Connection,
    *,
    model: str,
    semantic_query: str,
    tenant_id: str,
    k: int,
    vector_candidates: int,
    dynamic_predicates: list[Predicate],
    degraded_pred: str,
    exact_threshold: float,
) -> tuple[list[asyncpg.Record], str | None]:
    """In-database pgml.embed vector arm (exact or relaxed-order ANN scan)."""
    from corpus_kb.rag.self_query import build_filter_sql

    frag, params = build_filter_sql(dynamic_predicates, start_index=5)
    vector_limit = _candidate_limit(k, vector_candidates)

    async def fetch() -> tuple[list[asyncpg.Record], str | None]:
        use_exact = await _use_exact_scan(
            conn, dynamic_predicates, degraded_pred, tenant_id, exact_threshold
        )
        fetch_rows = _pgml_exact_rows if use_exact else _pgml_relaxed_rows
        vector_results = await fetch_rows(
            conn, model, semantic_query, tenant_id, vector_limit, frag, degraded_pred, params
        )
        return vector_results, None

    return await _run_in_savepoint(conn, fetch)
