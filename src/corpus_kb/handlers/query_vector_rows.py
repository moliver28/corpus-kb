"""Shared SQL scaffolding and fetch helpers for the vector-search arms.

Every vector arm reuses this module: the candidate SELECT list, the
dedup predicate, the FROM/JOIN spine fragments, the U21 count-probe
template, the savepoint fetch wrapper, the LIMIT contract, and the
selectivity probe.

The score ORDER BY clauses stay INLINE in each arm module on purpose:
the determinism audit counts them in the arm modules' source
(``tests/test_query_determinism.py``).

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
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import cast

import asyncpg

from corpus_kb.handlers.query_integrity import REASON_VECTOR_ARM_ERROR
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
    "chunks.file_path, chunks.start_line, chunks.end_line, chunks.chunk_index,"
    " chunks.heading_path, chunks.metadata"
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
