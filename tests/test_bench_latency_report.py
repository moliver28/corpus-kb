"""bench-latency gate math: p50 interpolation and ceiling-check strings."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

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
