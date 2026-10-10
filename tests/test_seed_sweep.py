"""U44 deterministic clustering — content hash, provenance, seed sweep."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

import numpy as np
import pytest

from corpus_kb.research.cluster_stability import ari, spherical_kmeans
from corpus_kb.research.release.seed_sweep import (
    MIN_SWEEP_SEEDS,
    clustering_provenance,
    content_hash_matrix,
    determinism_proof,
    library_versions,
    nmi,
    seed_sweep,
)

T = TypeVar("T")


def _blob_matrix(n_per: int = 12, seed: int = 7) -> list[list[float]]:
    """Two separable unit-normalized clusters plus a third tight pair."""
    rng = np.random.default_rng(seed)
    rows: list[list[float]] = []
    for center in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.9, 0.1, 0.0]):
        base = np.asarray(center) + 0.02 * rng.standard_normal((n_per, 3))
        norms = np.linalg.norm(base, axis=1, keepdims=True)
        rows.extend((base / norms).tolist())
    return rows


def test_content_hash_is_stable_and_order_sensitive():
    matrix = _blob_matrix(n_per=3)
    first = content_hash_matrix(matrix)
    second = content_hash_matrix([list(r) for r in matrix])
    assert first == second
    assert len(first) == 64
    reordered = content_hash_matrix(list(reversed(matrix)))
    assert reordered != first
    assert content_hash_matrix(matrix[:-1]) != first


def test_clustering_provenance_records_seed_params_versions():
    prov = clustering_provenance(42, {"n_components": 10, "metric": "cosine"})
    assert prov["seed"] == 42
    assert prov["transform_seed"] == 42
    assert prov["params"] == {"n_components": 10, "metric": "cosine"}
    versions = prov["library_versions"]
    assert isinstance(versions, dict) and "numpy" in versions
    assert "disabled by fixed random_state" in str(prov["umap_parallelism"])


def test_library_versions_marks_absent_packages():
    versions = library_versions(["definitely-not-a-package-xyz", "numpy"])
    assert versions["definitely-not-a-package-xyz"] is None
    assert versions["numpy"] is not None


def test_nmi_matches_ari_corner_cases():
    labels = [0, 0, 1, 1, 2, 2]
    assert nmi(labels, labels) == 1.0
    assert nmi([0, 0], [1, 1]) == 1.0
    swapped = [1, 1, 0, 0, 2, 2]
    assert nmi(labels, swapped) == 1.0
    assert ari(labels, swapped) == 1.0
    with pytest.raises(ValueError, match="align"):
        nmi([0, 1], [0, 1, 2])


def test_seed_sweep_is_deterministic_and_reports_distribution():
    matrix = _blob_matrix()
    sweep = seed_sweep(
        matrix,
        lambda m, seed: spherical_kmeans(m, 3, seed=seed),
        seeds=list(range(MIN_SWEEP_SEEDS)),
        max_count_variance=0.0,
    )
    assert sweep.n_seeds == MIN_SWEEP_SEEDS
    assert sweep.cluster_counts == dict.fromkeys(range(MIN_SWEEP_SEEDS), 3)
    assert sweep.pairwise_ari_mean == pytest.approx(1.0)
    assert sweep.pairwise_nmi_mean == pytest.approx(1.0)
    assert sweep.count_variance == pytest.approx(0.0)
    assert sweep.converged is True
    again = seed_sweep(
        matrix,
        lambda m, seed: spherical_kmeans(m, 3, seed=seed),
        seeds=list(range(MIN_SWEEP_SEEDS)),
        max_count_variance=0.0,
    )
    assert sweep.to_dict() == again.to_dict()


def test_seed_sweep_requires_ten_seeds():
    with pytest.raises(ValueError, match=">= 10"):
        seed_sweep(_blob_matrix(), lambda m, s: spherical_kmeans(m, 3, seed=s), seeds=[1, 2, 3])


def test_seed_sweep_not_converged_above_admin_threshold():
    matrix = _blob_matrix()

    def wobbly(m: Sequence[Sequence[float]], seed: int) -> list[int]:
        labels = spherical_kmeans(m, 2 + (seed % 3), seed=seed).tolist()
        return labels

    sweep = seed_sweep(matrix, wobbly, seeds=list(range(MIN_SWEEP_SEEDS)), max_count_variance=0.0)
    assert sweep.converged is False
    assert "not_converged" in sweep.note
    unjudged = seed_sweep(matrix, wobbly, seeds=list(range(MIN_SWEEP_SEEDS)))
    assert unjudged.converged is None
    assert "not_evaluable" in unjudged.note


def test_determinism_proof_assembles_the_manifest_block():
    matrix = _blob_matrix(n_per=4)
    proof = determinism_proof(
        matrix,
        seed=42,
        params={"n_components": 10},
        sweep=None,
        libraries={"numpy": "1.0"},
    )
    assert proof["embedding_matrix_sha256"] == content_hash_matrix(matrix)
    assert proof["seed"] == 42
    assert proof["library_versions"] == {"numpy": "1.0"}
    with_sweep = determinism_proof(
        matrix,
        seed=42,
        params={},
        sweep=seed_sweep(
            matrix, lambda m, s: spherical_kmeans(m, 3, seed=s), seeds=list(range(MIN_SWEEP_SEEDS))
        ),
    )
    sweep_block = with_sweep["sweep"]
    assert isinstance(sweep_block, dict)
    assert sweep_block["n_seeds"] == MIN_SWEEP_SEEDS
