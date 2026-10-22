"""Minimal bench harness test: 50 chunks, DuckDB only (WP sp-benchmark).

The full benchmark stays an operator-run script, never CI; these tests pin
the deterministic machinery -- corpus build, vector string determinism --
and run the harness end to end against a throwaway DuckDB file, asserting
it completes and emits a rows dict. Postgres legs stay out of CI: they
need a live server and minutes of wall time.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("duckdb")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name: str) -> Any:
    """Import one bench script by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


seed = _load("bench_storage_seed")
bench = _load("bench_storage")


def test_corpus_is_deterministic() -> None:
    a = seed.build_corpus(50)
    b = seed.build_corpus(50, seed=42)
    other = seed.build_corpus(50, seed=43)
    assert a.chunk_ids == b.chunk_ids
    assert a.texts == b.texts
    assert a.query_vectors_literal == b.query_vectors_literal
    assert a.chunk_ids != other.chunk_ids
    assert len(a.texts) == 50
    assert len(a.doc_ids) == 1


def test_vector_batches_are_deterministic() -> None:
    corpus = seed.build_corpus(64)
    first = seed.vector_batch_strings(0, 8, corpus)
    again = seed.vector_batch_strings(0, 8, corpus)
    later = seed.vector_batch_strings(8, 8, corpus)
    assert first == again
    assert first != later
    assert all(v.startswith("[") and v.endswith("]") for v in first)
    assert all(len(v.split(",")) == 2560 for v in first[:2])


async def test_harness_runs_duckdb_at_50_chunks(tmp_path: Path) -> None:
    duck_file = tmp_path / "bench.duckdb"
    cfg = bench.BenchConfig(
        sizes=[50],
        backends=["duckdb"],
        k=5,
        warmup=1,
        runs=3,
        seed=42,
        keep=False,
        bench_db="unused",
        duckdb_file=str(duck_file),
        json_out="",
    )
    out = await bench.run_duckdb(cfg, 50)

    assert out["chunks_per_sec"] > 0
    assert out["fts_rebuild_s"] > 0
    assert out["db_bytes"] > 0  # final file size, captured before deletion
    assert not duck_file.exists()
    assert set(out["ops"]) == {"knn", "lexical_fts", "hybrid_rrf", "list_documents", "fetch_chunk"}
    for stats in out["ops"].values():
        assert stats["median_s"] > 0
        assert stats["p95_s"] >= stats["median_s"]
        assert stats["runs"] == 3
    assert out["ops"]["knn"]["rows"] == 5
    assert out["ops"]["lexical_fts"]["rows"] > 0
    assert out["ops"]["hybrid_rrf"]["rows"] > 0
    assert out["ops"]["fetch_chunk"]["rows"] == 1
    assert out["ops"]["list_documents"]["rows"] == 1
