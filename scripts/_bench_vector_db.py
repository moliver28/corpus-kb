"""asyncpg harness for the vector-type benchmark.

Scratch-schema DDL, the three HNSW index generations, per-arm ANN query
text, and the timed measure loop. Pure database mechanics; the CLI and
the run orchestration stay in bench_vector_types.py, the math in
_bench_vector_metrics.py.
"""

from __future__ import annotations

import re
import time

import asyncpg
import numpy as np

from _bench_vector_metrics import (
    _mrl_256,
    _vector_literal,
    exact_topk,
    latency_percentiles,
    recall_at_k,
)

SCRATCH_SCHEMA = "bench_vector_types"
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", SCRATCH_SCHEMA):
    raise SystemExit("unsafe scratch schema name")


async def seed_items(
    cfg_dims: int, cfg_rows: int, conn: asyncpg.Connection, matrix: np.ndarray
) -> None:
    """Create the scratch items table and bulk-insert the corpus vectors."""
    await conn.execute(
        f'CREATE TABLE "{SCRATCH_SCHEMA}".items ('
        "id BIGSERIAL PRIMARY KEY, "
        f"embedding vector({cfg_dims}) NOT NULL, "
        "embedding_256 vector(256) NOT NULL)"
    )
    literals = [(_vector_literal(row), _vector_literal(_mrl_256(row))) for row in matrix[:cfg_rows]]
    await conn.executemany(
        f'INSERT INTO "{SCRATCH_SCHEMA}".items '
        "(embedding, embedding_256) VALUES ($1::vector, $2::vector)",
        literals,
    )


async def build_indexes(cfg_dims: int, conn: asyncpg.Connection) -> dict[str, dict[str, float]]:
    """Create the three HNSW index generations; time builds, capture bytes."""
    index_statements: dict[str, tuple[str, str]] = {
        "vector_full": (
            f'CREATE INDEX idx_bench_full ON "{SCRATCH_SCHEMA}".items '
            f"USING hnsw (embedding vector_cosine_ops)",
            f"{SCRATCH_SCHEMA}.idx_bench_full",
        ),
        "halfvec_cast": (
            f'CREATE INDEX idx_bench_half ON "{SCRATCH_SCHEMA}".items '
            f"USING hnsw ((embedding::halfvec({cfg_dims})) halfvec_cosine_ops)",
            f"{SCRATCH_SCHEMA}.idx_bench_half",
        ),
        "probe_256": (
            f'CREATE INDEX idx_bench_256 ON "{SCRATCH_SCHEMA}".items '
            f"USING hnsw (embedding_256 vector_cosine_ops)",
            f"{SCRATCH_SCHEMA}.idx_bench_256",
        ),
    }
    indexes: dict[str, dict[str, float]] = {}
    for name, (statement, qualified_index) in index_statements.items():
        start = time.perf_counter()
        await conn.execute(statement)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        size_row = await conn.fetchrow(
            "SELECT pg_relation_size($1::regclass)::bigint AS bytes", qualified_index
        )
        indexes[name] = {
            "build_ms": elapsed_ms,
            "bytes": float(size_row["bytes"]) if size_row else 0.0,
        }
    return indexes


def arm_queries(cfg_dims: int) -> dict[str, str]:
    """One ANN query per arm (full vector, halfvec cast, 256-d probe)."""
    return {
        "vector_full": (
            f"""
            WITH candidate AS MATERIALIZED (
                SELECT id, embedding <=> $1::vector AS dist
                FROM "{SCRATCH_SCHEMA}".items
                ORDER BY embedding <=> $1::vector
                LIMIT $2
            )
            SELECT id FROM candidate ORDER BY dist + 0 ASC
            """
        ),
        "halfvec_cast": (
            f"""
            WITH candidate AS MATERIALIZED (
                SELECT id, embedding::halfvec({cfg_dims}) <=> $1::halfvec({cfg_dims}) AS dist
                FROM "{SCRATCH_SCHEMA}".items
                ORDER BY embedding::halfvec({cfg_dims}) <=> $1::halfvec({cfg_dims})
                LIMIT $2
            )
            SELECT id FROM candidate ORDER BY dist + 0 ASC
            """
        ),
        "probe_256": (
            f"""
            WITH candidate AS MATERIALIZED (
                SELECT id, embedding_256 <=> $1::vector(256) AS dist
                FROM "{SCRATCH_SCHEMA}".items
                ORDER BY embedding_256 <=> $1::vector(256)
                LIMIT $2
            )
            SELECT id FROM candidate ORDER BY dist + 0 ASC
            """
        ),
    }


async def measure_arms(
    cfg_k: int,
    conn: asyncpg.Connection,
    matrix: np.ndarray,
    queries: np.ndarray,
    cfg_dims: int,
) -> dict[str, dict[str, object]]:
    """Run every arm against every query; recall, latency, first EXPLAIN."""
    arms: dict[str, dict[str, object]] = {}
    for arm, sql in arm_queries(cfg_dims).items():
        samples: list[float] = []
        recalls: list[float] = []
        explain_text = ""
        for i, query in enumerate(queries):
            param = _vector_literal(query)
            if arm == "probe_256":
                param = _vector_literal(_mrl_256(query))
            start = time.perf_counter()
            rows = await conn.fetch(sql, param, cfg_k)
            samples.append((time.perf_counter() - start) * 1000.0)
            ann_ids = [int(r["id"]) for r in rows]
            # ids are 1-based BIGSERIAL; matrix rows are 0-based.
            exact_ids = [i + 1 for i in exact_topk(matrix, query, cfg_k)]
            recalls.append(recall_at_k(ann_ids, exact_ids))
            if i == 0:
                plan = await conn.fetch(
                    "EXPLAIN (FORMAT TEXT) "
                    + sql.replace("$1", repr(param)).replace("$2", str(cfg_k))
                )
                explain_text = "\n".join(str(r["QUERY PLAN"]) for r in plan)
        arms[arm] = {
            **latency_percentiles(samples),
            "recall_at_k": float(np.mean(recalls)) if recalls else 0.0,
            "explain_first_plan": explain_text[:2000],
        }
    return arms
