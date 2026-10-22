"""Storage backend benchmark: Postgres (HNSW) vs DuckDB (exact scan).

WP sp-benchmark (storage portability program, benchmark gate §10). Seeds a
SYNTHETIC corpus straight through the storage layer -- one-hot anchor
vectors, no embedder -- at 1k / 10k / 50k chunks, then times the shared
operations on both engines: vector kNN, lexical FTS, the hybrid rrf_fuse
round-trip, document listing pagination, single-chunk fetch, and the bulk
seed write. Warmup runs precede the timed runs; the report carries median
and P95 (nearest-rank) from ``time.perf_counter``.

Isolation: Postgres runs in a throwaway database per size, DuckDB in one
throwaway file; both are deleted after the run (``--keep`` inspects them).
Numbers are single-user local-machine measurements -- the report labels
them; they are not CI truths.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):  # direct script run: allow sibling imports
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg
import bench_storage_ops as ops
import bench_storage_seed as seed_mod
import bench_storage_stores as stores

from corpus_kb.storage.duckdb_fts import ensure_fts_index, rebuild_fts_index
from corpus_kb.storage.gateway import PostgresGateway, SqlGateway

SIZES = (1_000, 10_000, 50_000)
BACKENDS = ("postgres", "duckdb")


@dataclass(frozen=True)
class BenchConfig:
    sizes: list[int]
    backends: list[str]
    k: int
    warmup: int
    runs: int
    seed: int
    keep: bool
    bench_db: str
    duckdb_file: str
    json_out: str


def parse_args(argv: list[str] | None = None) -> BenchConfig:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", default=",".join(str(s) for s in SIZES))
    parser.add_argument("--backends", default=",".join(BACKENDS))
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--keep", action="store_true", help="keep the stores after the run")
    parser.add_argument("--bench-db", default="corpus_kb_bench")
    parser.add_argument("--duckdb-file", default="bench_storage_tmp.duckdb")
    parser.add_argument("--json-out", default="")
    raw = parser.parse_args(argv)
    return BenchConfig(
        sizes=[int(s) for s in raw.sizes.split(",")],
        backends=[b.strip() for b in raw.backends.split(",")],
        k=raw.k,
        warmup=raw.warmup,
        runs=raw.runs,
        seed=raw.seed,
        keep=raw.keep,
        bench_db=raw.bench_db,
        duckdb_file=raw.duckdb_file,
        json_out=raw.json_out,
    )


def _scope(gateway: SqlGateway, tenant: str) -> contextlib.AbstractAsyncContextManager:
    """One tenant-scoped read window: the RLS GUC on PG, a tx scope on DuckDB."""
    return gateway.transaction(tenant)


async def run_postgres(cfg: BenchConfig, n: int) -> dict:
    base_dsn, super_dsn, user = stores.pg_env()
    db = f"{cfg.bench_db}_{n}"
    await stores.pg_prepare(base_dsn, super_dsn, user, db)
    if cfg.keep:
        print(f"[keep] Postgres bench database left up: {base_dsn}/{db}")
    pool = await asyncpg.create_pool(f"{base_dsn}/{db}", min_size=1)
    try:
        corpus = seed_mod.build_corpus(n, seed=cfg.seed)
        seed_s = await seed_mod.seed_postgres(pool, corpus, ops.TENANT)
        async with pool.acquire() as conn:
            t0 = time.perf_counter()
            await conn.execute("ANALYZE")
            analyze_s = time.perf_counter() - t0
        gateway = PostgresGateway(pool)
        async with _scope(gateway, ops.TENANT) as g:
            plan = await ops.pg_explain(g, ops.TENANT, corpus.query_vectors_literal[0], cfg.k)
        reads = await ops.run_read_ops(
            lambda: _scope(gateway, ops.TENANT),
            corpus,
            False,
            k=cfg.k,
            warmup=cfg.warmup,
            runs=cfg.runs,
        )
        db_size = 0 if cfg.keep else await stores.pg_drop(super_dsn, db)
        return {
            "seed_write_s": seed_s,
            "chunks_per_sec": round(n / seed_s, 1) if seed_s else 0.0,
            "analyze_s": analyze_s,
            "knn_uses_hnsw_index": ops.pg_plan_uses_hnsw(plan),
            "db_bytes": db_size,
            "ops": reads,
        }
    finally:
        await pool.close()


async def run_duckdb(cfg: BenchConfig, n: int) -> dict:
    path = Path(cfg.duckdb_file)
    gateway = await stores.duck_prepare(path)
    try:
        corpus = seed_mod.build_corpus(n, seed=cfg.seed)
        seed_s = await seed_mod.seed_duckdb(gateway, corpus, ops.TENANT)
        await ensure_fts_index(gateway)
        t0 = time.perf_counter()
        await rebuild_fts_index(gateway)
        fts_rebuild_s = time.perf_counter() - t0
        reads = await ops.run_read_ops(
            lambda: _scope(gateway, ops.TENANT),
            corpus,
            True,
            k=cfg.k,
            warmup=cfg.warmup,
            runs=cfg.runs,
        )
        db_size = await stores.duck_teardown(gateway, path, cfg.keep)
        if cfg.keep:
            print(f"[keep] DuckDB bench file left in place: {path.resolve()}")
        return {
            "seed_write_s": seed_s,
            "chunks_per_sec": round(n / seed_s, 1) if seed_s else 0.0,
            "fts_rebuild_s": fts_rebuild_s,
            "db_bytes": db_size,
            "ops": reads,
        }
    finally:
        with contextlib.suppress(Exception):
            await gateway.close()


def meta(cfg: BenchConfig) -> dict:
    try:
        import duckdb

        duckdb_version: object = duckdb.__version__
    except ImportError:
        duckdb_version = "not installed"
    return {
        "machine": platform.platform(),
        "python": sys.version.split()[0],
        "duckdb": duckdb_version,
        "dims": 2560,
        "k": cfg.k,
        "warmup": cfg.warmup,
        "runs": cfg.runs,
        "seed": cfg.seed,
        "tenant": ops.TENANT,
    }


def print_table(results: dict) -> None:
    for size, backends in results["sizes"].items():
        print(f"\n=== {size} chunks ===")
        print(f"{'op':<22}{'pg_med_s':>12}{'pg_p95_s':>12}{'duck_med_s':>13}{'duck_p95_s':>12}")
        pg = backends.get("postgres", {}).get("ops", {})
        duck = backends.get("duckdb", {}).get("ops", {})
        for op_name in ("knn", "lexical_fts", "hybrid_rrf", "list_documents", "fetch_chunk"):
            row = f"{op_name:<22}"
            for side in (pg, duck):
                stats = side.get(op_name, {})
                row += f"{stats.get('median_s', float('nan')):>12.5f}"
                row += f"{stats.get('p95_s', float('nan')):>12.5f}"
            print(row)
        for backend in ("postgres", "duckdb"):
            info = backends.get(backend, {})
            extra = f" hnsw={info.get('knn_uses_hnsw_index')}" if backend == "postgres" else ""
            print(
                f"seed_write: {backend:<12}{info.get('seed_write_s', 0):>10.2f}s"
                f"  {info.get('chunks_per_sec', 0):>10} chunks/s  db_bytes={info.get('db_bytes')}"
                f"{extra}"
            )


async def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    results: dict = {"meta": meta(cfg), "sizes": {}}
    for n in cfg.sizes:
        results["sizes"][n] = {}
        for backend in cfg.backends:
            runner = run_postgres if backend == "postgres" else run_duckdb
            print(f"[bench] {backend} @ {n} chunks ...", flush=True)
            results["sizes"][n][backend] = await runner(cfg, n)
    print_table(results)
    payload = json.dumps(results, indent=2)
    if cfg.json_out:
        Path(cfg.json_out).write_text(payload, encoding="utf-8")
        print(f"[done] JSON report: {cfg.json_out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
