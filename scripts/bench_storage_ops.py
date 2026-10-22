"""The timed storage operations, one SQL shape per engine (WP sp-benchmark).

Each op runs inside the caller's tenant scope (Postgres: a transaction with
the RLS GUC set; DuckDB: the gateway transaction scope). The vector and
lexical shapes mirror the shipped arms in handlers/query_vector_arms.py,
handlers/query_search_pg.py and storage/duckdb_search.py; the hybrid
round-trip fuses through the engine-neutral pure-Python ``rrf_fuse``
(handlers/query_fusion.py) that QueryHandler runs on BOTH engines. The
Postgres kNN plan is captured with EXPLAIN so the report can state whether
the HNSW index or a sequential scan served it.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from statistics import median
from typing import Any, cast

from corpus_kb.handlers.query_fusion import RowLike, rrf_fuse
from corpus_kb.storage.dialect import DUCKDB
from corpus_kb.storage.duckdb_support import Row
from corpus_kb.storage.gateway import SqlGateway

#: The one bench tenant (RLS-scoped on Postgres; an explicit predicate on
#: DuckDB). Fixed so every size and engine reads the same logical corpus.
TENANT = "00000000-0000-0000-0000-00000000b105"

#: The production arm SELECT list (handlers/query_vector_arms.py).
SELECT_LIST = (
    "chunks.chunk_id, chunks.text, chunks.doc_id, documents.source, chunks.file_path,"
    " chunks.start_line, chunks.end_line, chunks.chunk_index, chunks.heading_path"
)
DEDUP_PREDICATE = "AND chunks.tombstoned_at IS NULL AND chunks.superseded_at IS NULL"

_PG_DIST = "cv.embedding <=> $1::halfvec"
_DUCK_DIST = DUCKDB.vector_distance("cv.embedding", "$1")
_DUCK_ZERO_FLAG = f"CASE WHEN NOT ({DUCKDB.zero_vector_guard('cv.embedding')}) THEN 1 ELSE 0 END"
_BM25 = DUCKDB.fts_rank("chunks.chunk_id", "$1")
_FTS_VECTOR = "to_tsvector('english', coalesce(chunks.context_blurb,'') || ' ' || chunks.text)"

_KNN_PG_SQL = f"""
WITH candidate AS MATERIALIZED (
    SELECT cv.chunk_id AS candidate_chunk_id, 1 - ({_PG_DIST}) AS score
    FROM chunks_vectors cv
    JOIN chunks ON cv.chunk_id = chunks.chunk_id
    JOIN documents ON chunks.doc_id = documents.doc_id
    WHERE cv.tenant_id = $2 {DEDUP_PREDICATE}
    ORDER BY {_PG_DIST}
    LIMIT $3
)
SELECT {SELECT_LIST}, candidate.score AS score
FROM candidate
JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
ORDER BY candidate.score + 0 DESC, chunks.chunk_id ASC
LIMIT $3
"""

_KNN_DUCK_SQL = f"""
WITH candidate AS MATERIALIZED (
    SELECT cv.chunk_id AS candidate_chunk_id,
           {_DUCK_DIST} AS dist,
           {_DUCK_ZERO_FLAG} AS zero_flag
    FROM chunks_vectors cv
    JOIN chunks ON cv.chunk_id = chunks.chunk_id
    JOIN documents ON chunks.doc_id = documents.doc_id
    WHERE cv.tenant_id = $2 {DEDUP_PREDICATE}
)
SELECT {SELECT_LIST}, (1 - candidate.dist) AS score
FROM candidate
JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
ORDER BY candidate.zero_flag ASC, (1 - candidate.dist) DESC, chunks.chunk_id ASC
LIMIT $3
"""

_FTS_PG_SQL = f"""
SELECT {SELECT_LIST}, ts_rank({_FTS_VECTOR}, plainto_tsquery('english', $1)) AS score
FROM chunks
JOIN documents ON chunks.doc_id = documents.doc_id
WHERE chunks.tenant_id = $2 {DEDUP_PREDICATE}
  AND {_FTS_VECTOR} @@ plainto_tsquery('english', $1)
ORDER BY score DESC, chunks.chunk_id ASC
LIMIT $3
"""

_FTS_DUCK_SQL = f"""
SELECT {SELECT_LIST}, scored.score
FROM (
    SELECT chunks.chunk_id, {_BM25} AS score
    FROM chunks
) AS scored
JOIN chunks ON scored.chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
WHERE scored.score IS NOT NULL
  AND chunks.tenant_id = $2 {DEDUP_PREDICATE}
ORDER BY scored.score DESC, chunks.chunk_id ASC
LIMIT $3
"""

_LIST_PG_SQL = """
SELECT doc_id, source, source_type, chunk_count, created_at
FROM documents
WHERE tenant_id = $1
ORDER BY created_at DESC
LIMIT $2 OFFSET $3
"""

_LIST_DUCK_SQL = """
SELECT doc_id, source, source_type,
       (SELECT count(*) FROM chunks
         WHERE chunks.doc_id = documents.doc_id) AS chunk_count,
       created_at::VARCHAR AS created_at
FROM documents
WHERE tenant_id = $1
ORDER BY created_at DESC, doc_id ASC
LIMIT $2 OFFSET $3
"""

_FETCH_SQL = """
SELECT c.chunk_id, c.text, c.doc_id, c.chunk_index,
       (v.chunk_id IS NOT NULL) AS has_vector
FROM chunks c LEFT JOIN chunks_vectors v ON v.chunk_id = c.chunk_id
WHERE c.tenant_id = $1 AND c.chunk_id = $2
"""


async def knn(g: SqlGateway, tenant_id: str, vec_literal: str, k: int, duck: bool) -> list[Row]:
    """Top-k vector neighbors: HNSW arm on Postgres, exact scan on DuckDB."""
    sql = _KNN_DUCK_SQL if duck else _KNN_PG_SQL
    return list(await g.fetch(sql, vec_literal, tenant_id, k))


async def lexical(g: SqlGateway, tenant_id: str, query: str, k: int, duck: bool) -> list[Row]:
    """Top-k lexical matches: GIN ts_rank on Postgres, bm25 macro on DuckDB."""
    sql = _FTS_DUCK_SQL if duck else _FTS_PG_SQL
    return list(await g.fetch(sql, query, tenant_id, k))


async def hybrid(
    g: SqlGateway,
    tenant_id: str,
    vec_literal: str,
    query: str,
    k: int,
    duck: bool,
    rrf_k: int = 60,
) -> list[Any]:
    """The production hybrid round-trip: both arms + pure-Python rrf_fuse."""
    dense = await knn(g, tenant_id, vec_literal, k, duck)
    lex = await lexical(g, tenant_id, query, k, duck)
    # asyncpg Records and gateway dicts both satisfy RowLike at runtime
    # (the cast mirrors handlers/query_stages.py).
    fused = rrf_fuse(
        lexical=cast("Sequence[RowLike]", lex),
        dense=cast("Sequence[RowLike]", dense),
        k=k,
        rrf_k=rrf_k,
    )
    return list(fused)


async def list_documents(
    g: SqlGateway, tenant_id: str, limit: int, offset: int, duck: bool
) -> list[Row]:
    """One page of the document listing (production LIMIT/OFFSET shapes)."""
    sql = _LIST_DUCK_SQL if duck else _LIST_PG_SQL
    return list(await g.fetch(sql, tenant_id, limit, offset))


async def fetch_chunk(g: SqlGateway, tenant_id: str, chunk_id: str) -> Row | None:
    """Single-chunk fetch by id, with its vector presence (same SQL both)."""
    return await g.fetchrow(_FETCH_SQL, tenant_id, chunk_id)


async def pg_explain(g: SqlGateway, tenant_id: str, vec_literal: str, k: int) -> list[str]:
    """EXPLAIN lines of the Postgres kNN arm (planner-choice honesty)."""
    rows = await g.fetch("EXPLAIN (COSTS OFF) " + _KNN_PG_SQL, vec_literal, tenant_id, k)
    return [str(row["QUERY PLAN"]) for row in rows]


def pg_plan_uses_hnsw(plan_lines: list[str]) -> bool:
    """True when the captured plan names the halfvec HNSW index."""
    return any("idx_chunks_vectors_hnsw" in line for line in plan_lines)


def summarize(deltas: list[float]) -> dict[str, float | int]:
    """Median, P95 (nearest-rank), run count and total over timed deltas."""
    ordered = sorted(deltas)
    p95 = ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))]
    return {"median_s": median(ordered), "p95_s": p95, "runs": len(ordered)}


ScopeFactory = Callable[[], AbstractAsyncContextManager[SqlGateway]]


async def run_read_ops(
    scope: ScopeFactory,
    corpus: Any,
    duck: bool,
    *,
    k: int,
    warmup: int,
    runs: int,
) -> dict[str, dict[str, float | int]]:
    """Warm up, then time every read op over rotating query fixtures.

    ``scope()`` opens one tenant-scoped read window (Postgres: transaction
    with the RLS GUC; DuckDB: gateway transaction scope) held across all
    ops -- read-only, so the hold is free.
    """
    results: dict[str, dict[str, float | int]] = {}
    async with scope() as g:
        vecs = itertools.cycle(corpus.query_vectors_literal)
        texts = itertools.cycle(corpus.query_texts)
        chunk_ids = itertools.cycle(corpus.chunk_ids)

        async def time_one(name: str, op: Callable[[], Awaitable[list[Any] | Row | None]]) -> None:
            for _ in range(warmup):
                await op()
            deltas: list[float] = []
            rows_seen = 0
            for _ in range(runs):
                t0 = time.perf_counter()
                rows = await op()
                deltas.append(time.perf_counter() - t0)
                if isinstance(rows, list):
                    rows_seen = max(rows_seen, len(rows))
                else:
                    rows_seen = max(rows_seen, 1 if rows is not None else 0)
            stats = summarize(deltas)
            stats["rows"] = rows_seen
            results[name] = stats

        await time_one("knn", lambda: knn(g, TENANT, next(vecs), k, duck))
        await time_one("lexical_fts", lambda: lexical(g, TENANT, next(texts), k, duck))
        await time_one("hybrid_rrf", lambda: hybrid(g, TENANT, next(vecs), next(texts), k, duck))
        await time_one("list_documents", lambda: list_documents(g, TENANT, 20, 0, duck))
        await time_one("fetch_chunk", lambda: fetch_chunk(g, TENANT, next(chunk_ids)))
    return results
