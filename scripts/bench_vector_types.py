"""U22/U46 benchmark: full `vector` vs `halfvec` HNSW vs 256-d MRL probe.

Runs against a live Postgres+pgvector (default: the compose dev database)
in a scratch schema it creates and drops (``--keep`` to inspect). Seeded
synthetic corpus — the RUN is deterministic (fixed --seed), the NUMBERS are
local-machine measurements, not CI truths, and are labeled as such in the
report.

Measured per index generation: build time, index bytes, recall@k vs the
exact (numpy) ground truth, p50/p95 query latency under the configured
HNSW scan mode, and a captured EXPLAIN plan.

Binary quantization: deliberately REJECTED, no code path (spec v8 U46 —
needs heavy oversampling + re-rank and reported low recall).

U46 adoption gate (see adoption_decision): adopt halfvec on the main path
only when recall loss is within tolerance (1 point) AND the size/build
gains are material (>= 25% reduction on at least one). Adoption itself is
an admin decision; this script informs it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass, field

import asyncpg
import numpy as np

DEFAULT_DSN = "postgresql://corpus:corpus@localhost:5432/corpus"
SCRATCH_SCHEMA = "bench_vector_types"
LABEL = "local-machine measurement, not CI truth"
BINARY_QUANTIZATION_NOTE = (
    "rejected: needs heavy oversampling + re-rank and reported low recall (spec v8 U46); "
    "no code path shipped"
)
# U46 gate constants: admin tolerance (recall points) + material-gain bar.
RECALL_TOLERANCE_POINTS = 1.0
MIN_MATERIAL_GAIN_RATIO = 0.25


@dataclass(frozen=True)
class BenchConfig:
    dims: int = 1024
    rows: int = 2000
    probes: int = 20
    k: int = 10
    seed: int = 42
    scan_mode: str = "strict_order"
    keep: bool = False
    dsn: str = DEFAULT_DSN
    label: str = LABEL


@dataclass
class Decision:
    """U46 halfvec adoption gate verdict (admin decides; this informs)."""

    decision: str
    reasons: list[str] = field(default_factory=list)


def parse_args(argv: list[str] | None = None) -> BenchConfig:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--dims", type=int, default=1024)
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--probes", type=int, default=20)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--scan-mode", choices=["off", "relaxed_order", "strict_order"], default="strict_order"
    )
    parser.add_argument("--keep", action="store_true", help="keep the scratch schema")
    parser.add_argument("--label", default=LABEL)
    args = parser.parse_args(argv)
    if args.dims <= 0 or args.rows <= 0 or args.probes <= 0 or args.k <= 0:
        parser.error("--dims/--rows/--probes/--k must be positive")
    if args.k > args.rows:
        parser.error("--k cannot exceed --rows")
    return BenchConfig(
        dims=args.dims,
        rows=args.rows,
        probes=args.probes,
        k=args.k,
        seed=args.seed,
        scan_mode=args.scan_mode,
        keep=args.keep,
        dsn=args.dsn,
        label=args.label,
    )


def seeded_unit_vectors(rows: int, dims: int, seed: int) -> np.ndarray:
    """Deterministic row-normalized matrix (the ground-truth corpus)."""
    rng = np.random.default_rng(seed)
    vectors = rng.standard_normal((rows, dims)).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def seeded_queries(vectors: np.ndarray, probes: int, seed: int) -> np.ndarray:
    """Queries drawn from the corpus itself (guaranteed nearest neighbors)."""
    rng = random.Random(seed)
    picks = sorted(rng.sample(range(vectors.shape[0]), min(probes, vectors.shape[0])))
    return vectors[picks]


def exact_topk(matrix: np.ndarray, query: np.ndarray, k: int) -> list[int]:
    """Exact cosine top-k ids (rows are unit vectors, so dot == cosine)."""
    sims = matrix @ query
    return [int(i) for i in np.argsort(-sims)[:k]]


def recall_at_k(ann_ids: list[int], exact_ids: list[int]) -> float:
    if not exact_ids:
        return 0.0
    return len(set(ann_ids) & set(exact_ids)) / len(set(exact_ids))


def latency_percentiles(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"p50_ms": 0.0, "p95_ms": 0.0}
    arr = np.asarray(samples_ms, dtype=np.float64)
    return {"p50_ms": float(np.percentile(arr, 50)), "p95_ms": float(np.percentile(arr, 95))}


def adoption_decision(
    recall_loss_points: float,
    size_reduction_ratio: float,
    build_time_reduction_ratio: float,
    recall_tolerance_points: float = RECALL_TOLERANCE_POINTS,
    min_material_gain: float = MIN_MATERIAL_GAIN_RATIO,
) -> Decision:
    """U46 gate: recall loss <= tolerance AND a material size/build gain.

    ``*_reduction_ratio`` are fractions (0.4 = 40% smaller/faster). Never
    adopts on recall alone; never adopts with a recall loss over tolerance.
    """
    reasons: list[str] = []
    recall_ok = recall_loss_points <= recall_tolerance_points
    material = (
        size_reduction_ratio >= min_material_gain or build_time_reduction_ratio >= min_material_gain
    )
    if not recall_ok:
        reasons.append(
            f"recall loss {recall_loss_points:.2f}pt exceeds tolerance "
            f"{recall_tolerance_points:.2f}pt"
        )
    if not material:
        reasons.append(
            f"gains not material: size {size_reduction_ratio:.0%}, "
            f"build {build_time_reduction_ratio:.0%} < {min_material_gain:.0%}"
        )
    if recall_ok and material:
        reasons.append("recall within tolerance and gains are material")
    return Decision(decision="adopt" if (recall_ok and material) else "keep", reasons=reasons)


def _vector_literal(vector: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.9g}" for x in vector) + "]"


def _mrl_256(vector: np.ndarray) -> np.ndarray:
    slice_ = vector[:256]
    norm = float(np.linalg.norm(slice_))
    return slice_ / norm if norm > 0 else slice_


async def run_bench(cfg: BenchConfig, conn: asyncpg.Connection) -> dict[str, object]:
    """Create the scratch corpus, index it three ways, measure, drop."""
    matrix = seeded_unit_vectors(cfg.rows, cfg.dims, cfg.seed)
    queries = seeded_queries(matrix, cfg.probes, cfg.seed)
    await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCRATCH_SCHEMA}"')
    try:
        await conn.execute(
            f'CREATE TABLE "{SCRATCH_SCHEMA}".items ('
            "id BIGSERIAL PRIMARY KEY, "
            f"embedding vector({cfg.dims}) NOT NULL, "
            "embedding_256 vector(256) NOT NULL)"
        )
        literals = [
            (_vector_literal(row), _vector_literal(_mrl_256(row))) for row in matrix[: cfg.rows]
        ]
        await conn.executemany(
            f'INSERT INTO "{SCRATCH_SCHEMA}".items '
            "(embedding, embedding_256) VALUES ($1::vector, $2::vector)",
            literals,
        )

        indexes: dict[str, dict[str, float]] = {}
        index_statements: dict[str, tuple[str, str]] = {
            "vector_full": (
                f'CREATE INDEX idx_bench_full ON "{SCRATCH_SCHEMA}".items '
                f"USING hnsw (embedding vector_cosine_ops)",
                f"{SCRATCH_SCHEMA}.idx_bench_full",
            ),
            "halfvec_cast": (
                f'CREATE INDEX idx_bench_half ON "{SCRATCH_SCHEMA}".items '
                f"USING hnsw ((embedding::halfvec({cfg.dims})) halfvec_cosine_ops)",
                f"{SCRATCH_SCHEMA}.idx_bench_half",
            ),
            "probe_256": (
                f'CREATE INDEX idx_bench_256 ON "{SCRATCH_SCHEMA}".items '
                f"USING hnsw (embedding_256 vector_cosine_ops)",
                f"{SCRATCH_SCHEMA}.idx_bench_256",
            ),
        }
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

        arms: dict[str, dict[str, object]] = {}
        arm_queries = {
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
                    SELECT id, embedding::halfvec({cfg.dims}) <=> $1::halfvec({cfg.dims}) AS dist
                    FROM "{SCRATCH_SCHEMA}".items
                    ORDER BY embedding::halfvec({cfg.dims}) <=> $1::halfvec({cfg.dims})
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
        for arm, sql in arm_queries.items():
            samples: list[float] = []
            recalls: list[float] = []
            explain_text = ""
            for i, query in enumerate(queries):
                param = _vector_literal(query)
                if arm == "probe_256":
                    param = _vector_literal(_mrl_256(query))
                start = time.perf_counter()
                rows = await conn.fetch(sql, param, cfg.k)
                samples.append((time.perf_counter() - start) * 1000.0)
                ann_ids = [int(r["id"]) for r in rows]
                # ids are 1-based BIGSERIAL; matrix rows are 0-based.
                exact_ids = [i + 1 for i in exact_topk(matrix, query, cfg.k)]
                recalls.append(recall_at_k(ann_ids, exact_ids))
                if i == 0:
                    plan = await conn.fetch(
                        "EXPLAIN (FORMAT TEXT) "
                        + sql.replace("$1", repr(param)).replace("$2", str(cfg.k))
                    )
                    explain_text = "\n".join(str(r["QUERY PLAN"]) for r in plan)
            arms[arm] = {
                **latency_percentiles(samples),
                "recall_at_k": float(np.mean(recalls)) if recalls else 0.0,
                "explain_first_plan": explain_text[:2000],
            }

        full_size = indexes["vector_full"]["bytes"]
        half_size = indexes["halfvec_cast"]["bytes"]
        full_build = indexes["vector_full"]["build_ms"]
        half_build = indexes["halfvec_cast"]["build_ms"]
        gate = adoption_decision(
            recall_loss_points=max(
                0.0,
                float(arms["vector_full"]["recall_at_k"])
                - float(arms["halfvec_cast"]["recall_at_k"]),
            )
            * 100.0,
            size_reduction_ratio=(full_size - half_size) / full_size if full_size else 0.0,
            build_time_reduction_ratio=(full_build - half_build) / full_build
            if full_build
            else 0.0,
        )
        return {
            "status": "ok",
            "label": cfg.label,
            "config": {
                "dims": cfg.dims,
                "rows": cfg.rows,
                "probes": cfg.probes,
                "k": cfg.k,
                "seed": cfg.seed,
                "scan_mode": cfg.scan_mode,
            },
            "indexes": indexes,
            "arms": arms,
            "binary_quantization": BINARY_QUANTIZATION_NOTE,
            "adoption_gate": {
                "decision": gate.decision,
                "reasons": gate.reasons,
                "tolerance_points": RECALL_TOLERANCE_POINTS,
                "min_material_gain": MIN_MATERIAL_GAIN_RATIO,
                "note": (
                    "adoption creates a NEW index generation; the original "
                    "vector column is kept for rollback"
                ),
            },
        }
    finally:
        if not cfg.keep:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{SCRATCH_SCHEMA}" CASCADE')


async def _amain(cfg: BenchConfig) -> dict[str, object]:
    conn = await asyncpg.connect(cfg.dsn)
    try:
        async with conn.transaction():
            if cfg.scan_mode != "off":
                await conn.execute(f"SET LOCAL hnsw.iterative_scan = '{cfg.scan_mode}'")
            return await run_bench(cfg, conn)
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    report = asyncio.run(_amain(cfg))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
