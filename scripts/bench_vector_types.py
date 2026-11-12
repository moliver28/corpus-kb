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
an admin decision; this script informs it. The measurement math lives in
``_bench_vector_metrics``, the asyncpg harness in ``_bench_vector_db``
(both re-exported here so this module's surface stays test-stable).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The metrics imports below are re-exports: the tests pin THIS module's
# surface (bvt.* attributes), so the moved names stay imported here.
from _bench_vector_db import SCRATCH_SCHEMA, build_indexes, measure_arms, seed_items
from _bench_vector_metrics import (
    MIN_MATERIAL_GAIN_RATIO,
    RECALL_TOLERANCE_POINTS,
    Decision,
    _mrl_256,
    adoption_decision,
    exact_topk,
    latency_percentiles,
    recall_at_k,
    seeded_queries,
    seeded_unit_vectors,
)

# hnsw.iterative_scan takes fixed words; the map keeps them out of f-strings.
_SCAN_MODE_SQL = {
    "relaxed_order": "SET LOCAL hnsw.iterative_scan = 'relaxed_order'",
    "strict_order": "SET LOCAL hnsw.iterative_scan = 'strict_order'",
}
LABEL = "local-machine measurement, not CI truth"
BINARY_QUANTIZATION_NOTE = (
    "rejected: needs heavy oversampling + re-rank and reported low recall (spec v8 U46); "
    "no code path shipped"
)
DEFAULT_DSN = "postgresql://corpus:corpus@localhost:5432/corpus"


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


async def run_bench(cfg: BenchConfig, conn: asyncpg.Connection) -> dict[str, object]:
    """Create the scratch corpus, index it three ways, measure, drop."""
    matrix = seeded_unit_vectors(cfg.rows, cfg.dims, cfg.seed)
    queries = seeded_queries(matrix, cfg.probes, cfg.seed)
    await conn.execute('CREATE SCHEMA IF NOT EXISTS "' + SCRATCH_SCHEMA + '"')
    try:
        await seed_items(cfg.dims, cfg.rows, conn, matrix)
        indexes = await build_indexes(cfg.dims, conn)
        arms = await measure_arms(cfg.k, conn, matrix, queries, cfg.dims)
        gate = _adoption_gate(indexes, arms)
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


def _adoption_gate(
    indexes: dict[str, dict[str, float]],
    arms: dict[str, dict[str, object]],
) -> Decision:
    """U46 verdict from the full-vs-halfvec recall and size/build deltas."""
    full_size = indexes["vector_full"]["bytes"]
    half_size = indexes["halfvec_cast"]["bytes"]
    full_build = indexes["vector_full"]["build_ms"]
    half_build = indexes["halfvec_cast"]["build_ms"]
    return adoption_decision(
        recall_loss_points=max(
            0.0,
            float(arms["vector_full"]["recall_at_k"]) - float(arms["halfvec_cast"]["recall_at_k"]),
        )
        * 100.0,
        size_reduction_ratio=(full_size - half_size) / full_size if full_size else 0.0,
        build_time_reduction_ratio=(full_build - half_build) / full_build if full_build else 0.0,
    )


async def _amain(cfg: BenchConfig) -> dict[str, object]:
    conn = await asyncpg.connect(cfg.dsn)
    try:
        async with conn.transaction():
            if cfg.scan_mode != "off":
                await conn.execute(_SCAN_MODE_SQL[cfg.scan_mode])
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
