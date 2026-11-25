"""The bench-latency gate: measure the four spec paths, compare p50s to ceilings.

Spec section 9 (master-db program): one committed script measures all
four paths on scratch DuckDB stores. ``--check`` (the poe bench-latency
gate) exits 1 when any measured p50 sits over its ``bench_*`` ceiling in
``scripts/quality-baselines.json``; exact equality passes. ``--write``
prints the measured p50s as one JSON line, and ``--check`` prints the
same line before comparing. Every number is a measurement of the machine
that runs this script. Ceilings move only down, in the PR that speeds
the measured path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bench_latency_paths import (  # noqa: E402
    cold_start_samples,
    ingest_samples,
    matrix_sample,
    route_samples,
)

BASELINE_KEYS = (
    "bench_cold_start_ready_p50_s",
    "bench_first_query_p50_s",
    "bench_ingest_doc_p50_ms",
    "bench_matrix_op_p50_s",
    "bench_route_p50_ms",
)
BASELINES_PATH = Path(__file__).resolve().parent / "quality-baselines.json"
COLD_START_DEFAULT_ITERATIONS = 5
OTHER_DEFAULT_ITERATIONS = 30
BENCH_BASE_PORT = 8901
MS_PER_S = 1000.0


def percentile(values: list[float], pct: float) -> float:
    """The p-quantile by linear interpolation; empty input is 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (pct / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def load_ceilings() -> dict[str, float]:
    """The ``bench_*`` ceiling keys from the checked-in baselines file."""
    raw: dict[str, Any] = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    return {key: float(raw[key]) for key in BASELINE_KEYS if key in raw}


def check_against_ceilings(
    measured: dict[str, float], ceilings: dict[str, float]
) -> list[str]:
    """One violation line per measured p50 over its ceiling; equality passes."""
    return [
        f"{key} measured {measured[key]} over ceiling {ceilings[key]}"
        for key in measured
        if key in ceilings and measured[key] > ceilings[key]
    ]


async def measure(
    cold_iterations: int, other_iterations: int, skip_cold_start: bool
) -> dict[str, float]:
    """Run the drivers and return the p50s keyed by the baseline keys."""
    measured: dict[str, float] = {}
    if not skip_cold_start and cold_iterations > 0:
        pairs = await cold_start_samples(BENCH_BASE_PORT, cold_iterations)
        measured["bench_cold_start_ready_p50_s"] = percentile([pair[0] for pair in pairs], 50)
        measured["bench_first_query_p50_s"] = percentile([pair[1] for pair in pairs], 50)
    with tempfile.TemporaryDirectory(prefix="bench-latency-ingest-") as tmp:
        dsn = _migrated_scratch(Path(tmp))
        per_doc = await ingest_samples(dsn, other_iterations)
    measured["bench_ingest_doc_p50_ms"] = percentile(per_doc, 50) * MS_PER_S
    with tempfile.TemporaryDirectory(prefix="bench-latency-matrix-") as tmp:
        dsn = _migrated_scratch(Path(tmp))
        measured["bench_matrix_op_p50_s"] = await matrix_sample(dsn)
    routes = await route_samples(other_iterations)
    measured["bench_route_p50_ms"] = percentile(routes, 50)
    return measured


def _migrated_scratch(tmp: Path) -> str:
    """One migrated scratch DuckDB DSN inside ``tmp``."""
    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    dsn = duckdb_path_to_conn_string(tmp / "bench.duckdb")
    run_duckdb_migrations(dsn)
    return dsn


def main(argv: list[str] | None = None) -> int:
    """Parse flags, measure, print one JSON line, gate on ``--check``."""
    parser = argparse.ArgumentParser(
        description="Measure the P1-P4 latency paths and check p50 ceilings."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 when any measured p50 sits over its quality-baselines ceiling.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Print the measured p50s as one JSON line (the default action).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Samples per path (default: 5 cold start, 30 ingest and route).",
    )
    parser.add_argument(
        "--skip-cold-start",
        action="store_true",
        help="Skip the P1 boot drivers for a fast local loop.",
    )
    args = parser.parse_args(argv)
    cold = COLD_START_DEFAULT_ITERATIONS
    other = OTHER_DEFAULT_ITERATIONS
    if args.iterations is not None:
        cold = other = args.iterations
    measured = asyncio.run(measure(cold, other, args.skip_cold_start))
    print(json.dumps(measured, sort_keys=True))
    if not args.check:
        return 0
    violations = check_against_ceilings(measured, load_ceilings())
    for line in violations:
        print(line, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
