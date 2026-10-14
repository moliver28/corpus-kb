"""Vector-search arms for QueryHandler (zero-drift split of query_handler.py).

Each arm is ONE module function that runs one embedding provider's vector
retrieval and returns ``(rows, reason)``. ``rows`` is the candidate list the
RRF fusion consumes; ``reason`` is ``None`` on a clean run or the honesty
string for the search envelope's ``degraded_excluded`` (the arm never
raises for a failed fetch — it degrades and says so). SQL text here is
byte-identical to the pre-split inline arms; the recording-conn tests pin
it (tests/test_search_honesty.py, tests/test_vector_search_ordering.py).
"""

from __future__ import annotations

import logging

import asyncpg

from corpus_kb.handlers.query_integrity import (
    REASON_VECTOR_ARM_ERROR,
    REASON_ZERO_QUERY,
    is_all_zero,
)
from corpus_kb.rag.embedder import OllamaEmbedder
from corpus_kb.rag.self_query import Predicate
from corpus_kb.research.retrieval_settings import (
    estimate_selectivity,
    should_use_exact_scan,
)

logger = logging.getLogger(__name__)

# Shared candidate fragments. The literal table names ("chunks"/"documents")
# are NOT short aliases -- build_filter_sql() emits dynamic predicates using
# fully-qualified column refs like "documents.source_type" (see
# corpus_kb/rag/self_query.py ALLOWED_COLUMNS), so every arm must join these
# tables under their real names for those predicates to resolve.
PROVENANCE_COLS = (
    "chunks.file_path, chunks.start_line, chunks.end_line, chunks.chunk_index, chunks.heading_path"
)
SELECT_LIST = f"chunks.chunk_id, chunks.text, chunks.doc_id, documents.source, {PROVENANCE_COLS}"
DEDUP_PREDICATE = "AND chunks.tombstoned_at IS NULL AND chunks.superseded_at IS NULL"

# U21: paired-count selectivity probe over the same join shape as the vector
# arms. The filter fragment is formatted in with its own numbering starting
# at $2 (tenant is $1) so the probe is self-contained.
_COUNT_SQL_TEMPLATE = """
SELECT count(*)::float8 AS total,
       count(*) FILTER (WHERE TRUE {frag})::float8 AS matched
FROM chunks_vectors cv
JOIN chunks ON cv.chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
WHERE cv.tenant_id = $1 {dedup}
"""


async def _use_exact_scan(
    conn: asyncpg.Connection,
    dynamic_predicates: list[Predicate],
    degraded_pred: str,
    tenant_id: str,
    exact_threshold: float,
) -> bool:
    """U21: probe matched/total once and decide ANN vs filtered EXACT scan.

    With selective filters, HNSW can lose candidates (the ordered scan stops
    before matching rows are reached). Below the selectivity threshold the
    caller drops to a filtered EXACT scan — identical result schema, no ANN
    ordering. No filters = no probe (selectivity is 1.0 by definition).
    """
    from corpus_kb.rag.self_query import build_filter_sql

    selectivity = None
    if dynamic_predicates:
        probe_frag, probe_params = build_filter_sql(dynamic_predicates, start_index=2)
        selectivity = await estimate_selectivity(
            conn,
            _COUNT_SQL_TEMPLATE.format(
                frag=probe_frag, dedup=DEDUP_PREDICATE + " " + degraded_pred
            ),
            [tenant_id, *probe_params],
        )
    return should_use_exact_scan(bool(dynamic_predicates), selectivity, exact_threshold)


async def pgml_vector_arm(
    conn: asyncpg.Connection,
    *,
    model: str,
    semantic_query: str,
    tenant_id: str,
    k: int,
    over_retrieve_n: int,
    dynamic_predicates: list[Predicate],
    degraded_pred: str,
    exact_threshold: float,
) -> tuple[list[asyncpg.Record], str | None]:
    """In-database pgml.embed vector arm (exact or relaxed-order ANN scan)."""
    from corpus_kb.rag.self_query import build_filter_sql

    frag, params = build_filter_sql(dynamic_predicates, start_index=5)
    vector_results: list[asyncpg.Record] = []
    try:
        # Arm isolation (Wave 5 finding): the reads run inside a nested
        # transaction (a SAVEPOINT under the handler's transaction). A
        # failed statement (pgml.embed missing on a no-pgml host) rolls
        # back to the savepoint, so the shared handler transaction stays
        # alive and the lexical arm still runs. The exception keeps
        # propagating out of the block (the savepoint is rolled back
        # BEFORE the except below catches it) — the degrade contract
        # below is unchanged.
        async with conn.transaction():
            use_exact = await _use_exact_scan(
                conn, dynamic_predicates, degraded_pred, tenant_id, exact_threshold
            )
            if use_exact:
                # U40 pattern still holds: candidate set MATERIALIZED,
                # exact ordering applied OUTSIDE (`score + 0`).
                vector_results = await conn.fetch(
                    f"""
                    WITH candidate AS MATERIALIZED (
                        SELECT cv.chunk_id AS candidate_chunk_id,
                               1 - (cv.vector <=> (
                                   SELECT * FROM pgml.embed($1, ARRAY[$2]::text[]) LIMIT 1
                               )::vector) AS score
                        FROM chunks_vectors cv
                        JOIN chunks ON cv.chunk_id = chunks.chunk_id
                        JOIN documents ON chunks.doc_id = documents.doc_id
                        WHERE cv.tenant_id = $3 {DEDUP_PREDICATE} {frag} {degraded_pred}
                    )
                    SELECT {SELECT_LIST}, candidate.score AS score
                    FROM candidate
                    JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
                    JOIN documents ON chunks.doc_id = documents.doc_id
                    ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
                    LIMIT $4
                    """,
                    model,
                    semantic_query,
                    tenant_id,
                    max(k * 2, over_retrieve_n),
                    *params,
                )
            else:
                # U40 relaxed-ordering pattern (pgvector >= 0.8 iterative
                # scans): the ANN scan (filters + distance ORDER BY + LIMIT)
                # stays INSIDE a MATERIALIZED CTE; the exact ordering is
                # applied OUTSIDE over the retrieved set (`score + 0`
                # defeats planner sort-elision, per the pgvector docs).
                vector_results = await conn.fetch(
                    f"""
                WITH candidate AS MATERIALIZED (
                    SELECT cv.chunk_id AS candidate_chunk_id,
                           1 - (cv.vector <=> (
                               SELECT * FROM pgml.embed($1, ARRAY[$2]::text[]) LIMIT 1
                           )::vector) AS score
                    FROM chunks_vectors cv
                    JOIN chunks ON cv.chunk_id = chunks.chunk_id
                    JOIN documents ON chunks.doc_id = documents.doc_id
                    WHERE cv.tenant_id = $3 {DEDUP_PREDICATE} {frag} {degraded_pred}
                    ORDER BY cv.vector <=> (
                        SELECT * FROM pgml.embed($1, ARRAY[$2]::text[]) LIMIT 1
                    )::vector
                    LIMIT $4
                )
                SELECT {SELECT_LIST}, candidate.score AS score
                FROM candidate
                JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
                JOIN documents ON chunks.doc_id = documents.doc_id
                ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
                LIMIT $4
                """,
                    model,
                    semantic_query,
                    tenant_id,
                    max(k * 2, over_retrieve_n),
                    *params,
                )
    except Exception as exc:
        logger.warning("Vector search failed: %s", exc)
        return [], f"{REASON_VECTOR_ARM_ERROR} ({type(exc).__name__})"
    return vector_results, None


async def ollama_vector_arm(
    conn: asyncpg.Connection,
    *,
    embedder: OllamaEmbedder,
    semantic_query: str,
    tenant_id: str,
    k: int,
    over_retrieve_n: int,
    dynamic_predicates: list[Predicate],
    degraded_pred: str,
    matryoshka_enabled: bool,
    matryoshka_dim: int,
    candidate_multiplier: int,
    exact_threshold: float,
) -> tuple[list[asyncpg.Record], str | None]:
    """Local Ollama vector arm (optional matryoshka two-tier retrieval)."""
    vector_results: list[asyncpg.Record] = []
    try:
        # Arm isolation: same savepoint pattern as the pgml arm — this
        # arm's SQL can fail on a missing operator/function too, and the
        # rollback-to-savepoint must keep the shared handler transaction
        # alive for the lexical arm.
        async with conn.transaction():
            vector_limit = max(k * 2, over_retrieve_n)
            from corpus_kb.rag.self_query import build_filter_sql

            if matryoshka_enabled:
                q4096 = embedder.embed(semantic_query)
                frag, params = build_filter_sql(dynamic_predicates, start_index=6)
                candidate_n = k * int(candidate_multiplier)
                # Stage 3 zero-query guard: an all-zero query embedding
                # makes every cosine distance NaN (0/0), which sorts
                # FIRST under DESC — abstain instead of returning
                # garbage confident-looking hits. The lexical arm below
                # still runs.
                if is_all_zero(q4096):
                    return [], REASON_ZERO_QUERY
                q1024 = embedder.embed_matryoshka(semantic_query, matryoshka_dim)
                # U40: MATERIALIZED candidate CTE (1024-d probe scan) +
                # exact full-dimension re-ordering OUTSIDE the CTE.
                vector_results = await conn.fetch(
                    f"""
                    WITH cand AS MATERIALIZED (
                        SELECT cv.chunk_id, cv.vector
                        FROM chunks_vectors cv
                        JOIN chunks ON cv.chunk_id = chunks.chunk_id
                        JOIN documents ON chunks.doc_id = documents.doc_id
                        WHERE cv.tenant_id = $2 AND cv.vector_1024 IS NOT NULL
                          {DEDUP_PREDICATE} {frag} {degraded_pred}
                        ORDER BY cv.vector_1024 <=> $4::vector
                        LIMIT $5
                    )
                    SELECT {SELECT_LIST},
                           1 - (cand.vector <=> $1::vector) AS score
                    FROM cand
                    JOIN chunks ON cand.chunk_id = chunks.chunk_id
                    JOIN documents ON chunks.doc_id = documents.doc_id
                    ORDER BY (1 - (cand.vector <=> $1::vector)) + 0 DESC,
                             chunks.chunk_id ASC
                    LIMIT $3
                    """,
                    str(q4096),
                    tenant_id,
                    vector_limit,
                    str(q1024),
                    candidate_n,
                    *params,
                )
            else:
                query_vector = embedder.embed(semantic_query)
                frag, params = build_filter_sql(dynamic_predicates, start_index=4)
                # Stage 3 zero-query guard (see the matryoshka arm).
                if is_all_zero(query_vector):
                    return [], REASON_ZERO_QUERY
                # U21: same selectivity probe + exact-scan fallback as
                # the pgml arm (identical result schema and params).
                use_exact = await _use_exact_scan(
                    conn, dynamic_predicates, degraded_pred, tenant_id, exact_threshold
                )
                if use_exact:
                    # No distance ORDER BY/LIMIT inside the CTE: the
                    # planner cannot use the ordered HNSW scan here,
                    # so every matching row is scored exactly and the
                    # outer ORDER BY picks the true top-k.
                    vector_results = await conn.fetch(
                        f"""
                        WITH candidate AS MATERIALIZED (
                            SELECT cv.chunk_id AS candidate_chunk_id,
                                   1 - (cv.vector <=> $1::vector) AS score
                            FROM chunks_vectors cv
                            JOIN chunks ON cv.chunk_id = chunks.chunk_id
                            JOIN documents ON chunks.doc_id = documents.doc_id
                            WHERE cv.tenant_id = $2 {DEDUP_PREDICATE} {frag}
                              {degraded_pred}
                        )
                        SELECT {SELECT_LIST}, candidate.score AS score
                        FROM candidate
                        JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
                        JOIN documents ON chunks.doc_id = documents.doc_id
                        ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
                        LIMIT $3
                        """,
                        str(query_vector),
                        tenant_id,
                        vector_limit,
                        *params,
                    )
                else:
                    # U40 relaxed-ordering pattern: scan inside a
                    # MATERIALIZED CTE, exact ordering outside (`score + 0`).
                    vector_results = await conn.fetch(
                        f"""
                    WITH candidate AS MATERIALIZED (
                        SELECT cv.chunk_id AS candidate_chunk_id,
                               1 - (cv.vector <=> $1::vector) AS score
                        FROM chunks_vectors cv
                        JOIN chunks ON cv.chunk_id = chunks.chunk_id
                        JOIN documents ON chunks.doc_id = documents.doc_id
                        WHERE cv.tenant_id = $2 {DEDUP_PREDICATE} {frag}
                          {degraded_pred}
                        ORDER BY cv.vector <=> $1::vector
                        LIMIT $3
                    )
                    SELECT {SELECT_LIST}, candidate.score AS score
                    FROM candidate
                    JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
                    JOIN documents ON chunks.doc_id = documents.doc_id
                    ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
                    LIMIT $3
                    """,
                        str(query_vector),
                        tenant_id,
                        vector_limit,
                        *params,
                    )
    except Exception as exc:
        logger.warning("Vector search failed: %s", exc)
        return [], f"{REASON_VECTOR_ARM_ERROR} ({type(exc).__name__})"
    return vector_results, None
