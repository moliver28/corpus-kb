"""U22/U46 bench script tests: structure, determinism, and the adoption gate.

Real numbers come from running scripts/bench_vector_types.py against a live
Postgres+pgvector (operator-run, labeled local-only); these tests pin the
deterministic machinery — seeded vectors, exact top-k, recall, latency
shape, argument parsing, and the U46 adoption gate truth table.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bench_vector_types.py"

_spec = importlib.util.spec_from_file_location("bench_vector_types_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
bvt = importlib.util.module_from_spec(_spec)
sys.modules["bench_vector_types_test"] = bvt
_spec.loader.exec_module(bvt)


def test_parse_args_defaults_and_validation() -> None:
    cfg = bvt.parse_args([])
    assert (cfg.dims, cfg.rows, cfg.probes, cfg.k, cfg.seed) == (1024, 2000, 20, 10, 42)
    assert cfg.scan_mode == "strict_order"
    assert cfg.keep is False
    assert "local-machine" in cfg.label
    with pytest.raises(SystemExit):
        bvt.parse_args(["--rows", "0"])
    with pytest.raises(SystemExit):
        bvt.parse_args(["--rows", "10", "--k", "20"])


def test_seeded_vectors_are_deterministic_and_unit_norm() -> None:
    a = bvt.seeded_unit_vectors(32, 16, seed=42)
    b = bvt.seeded_unit_vectors(32, 16, seed=42)
    c = bvt.seeded_unit_vectors(32, 16, seed=43)
    assert np.allclose(a, b)
    assert not np.allclose(a, c)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)


def test_seeded_queries_are_stable_subsets() -> None:
    vectors = bvt.seeded_unit_vectors(50, 8, seed=1)
    q1 = bvt.seeded_queries(vectors, 5, seed=2)
    q2 = bvt.seeded_queries(vectors, 5, seed=2)
    assert np.allclose(q1, q2)
    assert q1.shape == (5, 8)


def test_exact_topk_matches_bruteforce_and_recall_computes() -> None:
    rng = np.random.default_rng(0)
    matrix = rng.standard_normal((100, 8)).astype(np.float32)
    query = matrix[17] + 0.01 * rng.standard_normal(8).astype(np.float32)
    top = bvt.exact_topk(matrix, query, 5)
    # The query is a perturbed copy of row 17: it must rank first.
    assert top[0] == 17
    assert len(top) == 5
    assert bvt.recall_at_k(top, top) == 1.0
    assert bvt.recall_at_k(top[:3], top) == pytest.approx(3 / 5)
    assert bvt.recall_at_k([], top) == 0.0


def test_latency_percentiles_shape() -> None:
    pct = bvt.latency_percentiles([1.0, 2.0, 3.0, 4.0, 100.0])
    assert pct["p50_ms"] == 3.0
    assert pct["p95_ms"] > 4.0
    assert bvt.latency_percentiles([]) == {"p50_ms": 0.0, "p95_ms": 0.0}


def test_adoption_decision_truth_table_u46() -> None:
    # Within tolerance + material size gain -> adopt.
    assert bvt.adoption_decision(0.5, 0.45, 0.10).decision == "adopt"
    # Within tolerance + material build gain -> adopt.
    assert bvt.adoption_decision(1.0, 0.10, 0.30).decision == "adopt"
    # Recall loss over tolerance -> keep, whatever the gains.
    kept = bvt.adoption_decision(1.01, 0.60, 0.60)
    assert kept.decision == "keep"
    assert any("tolerance" in r for r in kept.reasons)
    # Within tolerance but no material gain -> keep.
    poor = bvt.adoption_decision(0.2, 0.10, 0.10)
    assert poor.decision == "keep"
    assert any("material" in r for r in poor.reasons)
    # Boundary: exactly at tolerance and exactly at the gain bar adopts.
    assert bvt.adoption_decision(1.0, 0.25, 0.0).decision == "adopt"


def test_report_carries_rejection_note_and_gate_constants() -> None:
    assert "rejected" in bvt.BINARY_QUANTIZATION_NOTE
    assert bvt.RECALL_TOLERANCE_POINTS == 1.0
    assert bvt.MIN_MATERIAL_GAIN_RATIO == 0.25
    assert "local-machine" in bvt.LABEL


def test_mrl_256_slice_is_renormalized() -> None:
    vec = np.arange(300, dtype=np.float32)
    vec[0] = 300.0  # avoid the leading-zeros norm edge
    sliced = bvt._mrl_256(vec)
    assert sliced.shape == (256,)
    assert np.isclose(np.linalg.norm(sliced), 1.0)
