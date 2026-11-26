"""bench-latency gate math: p50 interpolation and ceiling-check strings."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

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


def test_p50_math_is_interpolated_and_deterministic() -> None:
    bench = _load("bench_latency")
    assert bench.percentile([3.0, 1.0, 2.0], 50) == 2.0
    assert bench.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert bench.percentile([], 50) == 0.0


def test_check_accepts_p50_at_ceiling_boundary(tmp_path: Any, monkeypatch: Any) -> None:
    bench = _load("bench_latency")
    ceilings = {"bench_route_p50_ms": 10.0}
    monkeypatch.setattr(bench, "load_ceilings", lambda: ceilings)
    measured = {"bench_route_p50_ms": 10.0}  # boundary equality passes
    assert bench.check_against_ceilings(measured, ceilings) == []
    measured_over = {"bench_route_p50_ms": 10.01}
    assert bench.check_against_ceilings(measured_over, ceilings) == [
        "bench_route_p50_ms measured 10.01 over ceiling 10.0"
    ]


def test_missing_ceiling_key_fails_the_gate_loudly(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """An absent bench_* ceiling fails the gate (RVW2-03), never skips a path."""
    bench = _load("bench_latency")

    async def _fake_measure(*args: Any, **kwargs: Any) -> dict[str, float]:
        return {"bench_route_p50_ms": 1.0}

    monkeypatch.setattr(bench, "measure", _fake_measure)
    path = tmp_path / "quality-baselines.json"
    path.write_text(json.dumps({"bench_route_p50_ms": 10.0}), encoding="utf-8")
    monkeypatch.setattr(bench, "BASELINES_PATH", path)
    assert bench.main(["--check", "--skip-cold-start"]) == 1
    assert "Missing bench ceilings" in capsys.readouterr().err


def test_offline_fts_skips_p1_with_typed_signal(monkeypatch: Any, capsys: Any) -> None:
    """An offline fts download skips P1 with the typed signal (RVW2-06)."""
    pytest.importorskip("duckdb")
    paths = _load("_bench_latency_paths")
    bench = _load("bench_latency")
    from corpus_kb._setup import setup_duckdb

    monkeypatch.setattr(setup_duckdb, "preinstall_extensions", lambda: 1)
    with pytest.raises(paths.OfflineSkipError):
        asyncio.run(paths.cold_start_samples(8901, 1))

    async def _offline(*args: Any, **kwargs: Any) -> dict[str, float]:
        raise paths.OfflineSkipError("The fts extension cannot download offline.")

    async def _fast_route(*args: Any, **kwargs: Any) -> list[float]:
        return [1.0]

    monkeypatch.setattr(bench, "cold_start_samples", _offline)
    monkeypatch.setattr(bench, "ingest_samples", _fast_route)
    monkeypatch.setattr(bench, "matrix_sample", _fast_route)
    monkeypatch.setattr(bench, "route_samples", _fast_route)
    measured = asyncio.run(bench.measure(2, 2, False))
    assert "bench_cold_start_ready_p50_s" not in measured
    assert measured["bench_route_p50_ms"] == 1.0
    assert "SKIP P1" in capsys.readouterr().err
