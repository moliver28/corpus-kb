"""Vector-search arms for QueryHandler (zero-drift split of query_handler.py).

Each arm is ONE module function that runs one embedding provider's vector
retrieval and returns ``(rows, reason)``. ``rows`` is the candidate list the
RRF fusion consumes; ``reason`` is ``None`` on a clean run or the honesty
string for the search envelope's ``degraded_excluded`` (the arm never
raises for a failed fetch — it degrades and says so).

Config v2 (dimension regime): the main column is ``chunks_vectors.embedding``
(``halfvec(2560)`` — halfvec is required because pgvector HNSW caps the
plain vector type at 2000 dims), so every probe casts the query vector to
``::halfvec`` and compares against ``cv.embedding``. The matryoshka arm's
``vector_1024`` slice stays a plain ``vector(1024)`` column.

Arm LIMIT contract (config v2 live wiring): the candidate depth is
``max(vector_candidates, k * 2)`` — ``retrieval.vector_candidates`` is the
live knob and ``k * 2`` is the floor that keeps small candidate configs
from under-fetching below k. The rerank pre-fetch width
(``reranking.over_retrieve_n``) is a POST-fusion concern owned by
QueryHandler and no longer inflates the arm LIMITs.

Shared scaffolding: ``_run_in_savepoint`` is the fetch wrapper (arm
isolation + the degrade contract), ``_candidate_limit`` is the LIMIT
contract, and the ``_CTE_SPINE`` / ``_CANDIDATE_SPINE`` fragments are the
repeated SQL join shapes. The score ORDER BY clauses stay INLINE in each
arm on purpose: the determinism audit counts them in this file's source
(``tests/test_query_determinism.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import cast

import asyncpg

from corpus_kb.handlers.query_integrity import (
    REASON_VECTOR_ARM_ERROR,
    REASON_ZERO_QUERY,
    is_all_zero,
)
from corpus_kb.rag.embedder import OllamaEmbedder
from corpus_kb.rag.self_query import Predicate
from corpus_kb.research.retrieval_settings import (
    SelectivityReader,
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

# Shared SQL scaffolding (format-only, no placeholders): the FROM/JOIN spine
# of every candidate CTE and the ranked outer stage's SELECT/join tail. The
# per-arm WHERE/LIMIT/ORDER BY text stays inline in each arm.
_CTE_SPINE = """
    FROM chunks_vectors cv
    JOIN chunks ON cv.chunk_id = chunks.chunk_id
    JOIN documents ON chunks.doc_id = documents.doc_id
"""
_CANDIDATE_SPINE = f"""
    SELECT {SELECT_LIST}, candidate.score AS score
    FROM candidate
    JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
    JOIN documents ON chunks.doc_id = documents.doc_id
"""

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


def _candidate_limit(k: int, vector_candidates: int) -> int:
    """The arm LIMIT contract: candidate depth = max(vector_candidates, k * 2)."""
    return max(vector_candidates, k * 2)


async def _run_in_savepoint(
    conn: asyncpg.Connection,
    fetch: Callable[[], Awaitable[tuple[list[asyncpg.Record], str | None]]],
) -> tuple[list[asyncpg.Record], str | None]:
    """Run one arm's fetch under its own savepoint; degrade honestly.

    Arm isolation (Wave 5 finding): the reads run inside a nested
    transaction (a SAVEPOINT under the handler's transaction). A failed
    statement (pgml.embed missing on a no-pgml host) rolls back to the
    savepoint, so the shared handler transaction stays alive and the
    lexical arm still runs. The exception keeps propagating out of the
    block (the savepoint is rolled back BEFORE the except below catches
    it) — the degrade contract is unchanged. The arm never raises for a
    failed fetch: it returns the honesty reason instead.
    """
    try:
        async with conn.transaction():
            return await fetch()
    except Exception as exc:
        logger.warning("Vector search failed: %s", exc)
        return [], f"{REASON_VECTOR_ARM_ERROR} ({type(exc).__name__})"


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
            cast("SelectivityReader", conn),
            _COUNT_SQL_TEMPLATE.format(
                frag=probe_frag, dedup=DEDUP_PREDICATE + " " + degraded_pred
            ),
            [tenant_id, *probe_params],
        )
    return should_use_exact_scan(bool(dynamic_predicates), selectivity, exact_threshold)


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
