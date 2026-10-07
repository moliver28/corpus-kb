"""Offline tests for exhaustiveness + cluster-stability math (todo 17)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.research.cluster_stability import (
    ari,
    bootstrap_stability,
    silhouette_by_group,
    spherical_kmeans,
)
from corpus_kb.research.exhaustiveness import (
    calibrate_tau_res,
    coverage_curve_slope,
    gold_within_code_similarities,
    percentile_bands,
    plateau_detected,
    residual_section,
    residuals,
)


def _unit(seed: int, dim: int = 16) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tolist()


def test_residuals_max_over_codes_prototypes_views():
    a = [1.0, 0.0]
    far = [0.0, 1.0]
    near = [0.9999, 0.0141]
    r = residuals(
        {"answer": [far, near], "qa": [far, far]},
        {"c1": {"answer": [a], "qa": [a]}},
    )
    assert float(r[0]) < 0.1
    assert float(r[1]) > 0.99


def test_gold_within_code_similarities_pairwise():
    a1, a2 = _unit(10), _unit(11)
    rng = np.random.default_rng(5)
    jitter = rng.normal(size=16) * 0.01
    a2j = (np.asarray(a1) + jitter).astype(np.float32)
    a2j = (a2j / np.linalg.norm(a2j)).tolist()
    sims = gold_within_code_similarities([[a1, a2j], [a2]])
    assert len(sims) == 1
    assert sims[0] > 0.95


def test_calibrate_tau_res_low_quantile_per_type():
    tight = [0.98, 0.99, 0.99, 0.97]
    wide = [0.6, 0.9, 0.75, 0.85]
    tau = calibrate_tau_res({"interview": tight, "meeting": wide})
    assert tau["interview"] > tau["meeting"]
    assert all(v >= 0.05 for v in tau.values())


def test_tau_res_degenerate_type_floors():
    tau = calibrate_tau_res({"solo": [0.9]})
    assert tau["solo"] == 0.05


def test_residual_section_r_and_percentiles():
    residuals_by_type = {"interview": [0.99, 0.30, 0.10, 0.05]}
    tau = {"interview": 0.20}
    section = residual_section(residuals_by_type, tau)
    block = dict(section["by_source_type"])["interview"]
    assert block["R"] == 0.5
    assert block["p10"] == pytest.approx(0.065)
    assert block["p50"] == pytest.approx(0.20)
    assert block["n"] == 4
    assert dict(section["pooled"])["n"] == 4


def test_percentile_bands_empty():
    assert percentile_bands([]) == {"p10": 0.0, "p50": 0.0, "p90": 0.0}


def test_coverage_curve_slope_and_plateau():
    assert coverage_curve_slope([0.3, 0.2, 0.1]) == pytest.approx(-0.1)
    assert coverage_curve_slope([0.1, 0.1]) == pytest.approx(0.0)
    assert coverage_curve_slope([0.4]) == 0.0
    assert plateau_detected(0.005)
    assert not plateau_detected(-0.1)


def test_ari_identical_and_disjoint_partitions():
    labels_a = [0, 0, 1, 1, 2, 2]
    labels_b = [5, 5, 7, 7, 9, 9]
    assert ari(labels_a, labels_b) == pytest.approx(1.0)
    mixed = [0, 1, 0, 1, 2, 2]
    assert ari(labels_a, mixed) < 0.5


def test_ari_noise_label_is_own_group():
    a = [0, 0, -1, -1]
    b = [0, 0, -1, -1]
    assert ari(a, b) == pytest.approx(1.0)


def test_spherical_kmeans_deterministic_and_separates():
    rng = np.random.default_rng(7)
    centers = [rng.normal(size=8).astype(np.float32) for _ in range(3)]
    centers = [c / np.linalg.norm(c) for c in centers]
    vectors = []
    for c in centers:
        for _ in range(6):
            v = c + 0.01 * rng.normal(size=8).astype(np.float32)
            vectors.append((v / np.linalg.norm(v)).tolist())
    labels1 = spherical_kmeans(vectors, 3, seed=42).tolist()
    labels2 = spherical_kmeans(vectors, 3, seed=42).tolist()
    assert labels1 == labels2
    assert ari(labels1, [i // 6 for i in range(18)]) == pytest.approx(1.0)


def test_bootstrap_stability_on_tight_themes():
    rng = np.random.default_rng(11)
    centers = [rng.normal(size=8).astype(np.float32) for _ in range(3)]
    centers = [c / np.linalg.norm(c) for c in centers]
    vectors = []
    for c in centers:
        for _ in range(8):
            v = c + 0.01 * rng.normal(size=8).astype(np.float32)
            vectors.append((v / np.linalg.norm(v)).tolist())
    report = bootstrap_stability(vectors, n_resamples=10, seed=42)
    assert report["n_resamples"] == 10
    assert float(report["mean_pairwise_ari"]) >= 0.75
    assert report["stable"] is True


def test_bootstrap_stability_too_few_units():
    report = bootstrap_stability([[1.0, 0.0], [0.0, 1.0]])
    assert report["n_resamples"] == 0
    assert report["stable"] is None


def test_silhouette_by_group_separated_clusters():
    vecs = [[1.0, 0.0], [0.99, 0.1], [0.0, 1.0], [0.1, 0.99]]
    labels = [0, 0, 1, 1]
    sil = silhouette_by_group(vecs, labels)
    assert sil[0] > 0.8
    assert sil[1] > 0.8


def test_silhouette_singleton_is_zero():
    sil = silhouette_by_group([[1.0, 0.0], [0.0, 1.0]], [0, 1])
    assert sil[0] == 0.0
    assert sil[1] == 0.0
