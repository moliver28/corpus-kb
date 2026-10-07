"""Offline tests for the inductive clustering math (todo 15, v5 §9)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from corpus_kb.coding.inductive_cluster import (
    CLUSTER_N_COMPONENTS,
    DBCV_DROP_THRESHOLD,
    UMAP_RANDOM_STATE,
    UMAP_TRANSFORM_SEED,
    centroid_drift,
    centroids_in_original_space,
    cluster_embeddings,
    dbcv_drop_flag,
    is_high_entropy,
    min_cluster_size_for,
    nearest_centroids,
    normalized_entropy,
    run_manifest,
    soft_assignment_entropy,
)
from corpus_kb.coding.inductive_summaries import SUMMARY_PROMPT_ID, SUMMARY_TEMPERATURE
from corpus_kb.research.inductive_run import inductive_config, suggested_label


def _vec(seed: int, dim: int = 8) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tolist()


def _blob(center_seed: int, member_seed: int, eps: float = 0.02) -> list[float]:
    center = np.asarray(_vec(center_seed))
    rng = np.random.default_rng(member_seed)
    v = center + eps * rng.normal(size=center.shape).astype(np.float32)
    v /= np.linalg.norm(v)
    return [float(x) for x in v]


def test_min_cluster_size_formula():
    assert min_cluster_size_for(400) == 5
    assert min_cluster_size_for(1000) == 10
    assert min_cluster_size_for(501) == 6
    assert min_cluster_size_for(10) == 5


def test_run_manifest_pins_all_determinism_knobs():
    manifest = run_manifest(400)
    assert manifest["umap_random_state"] == UMAP_RANDOM_STATE
    assert manifest["umap_transform_seed"] == UMAP_TRANSFORM_SEED
    assert manifest["n_components"] == CLUSTER_N_COMPONENTS
    assert manifest["metric"] == "cosine"
    assert manifest["min_cluster_size"] == 5
    assert manifest["min_samples"] == manifest["min_cluster_size"]
    assert manifest["dbcv_drop_threshold"] == DBCV_DROP_THRESHOLD


def test_centroids_in_original_space_exclude_noise_and_normalize():
    vectors = [_blob(1, 10), _blob(1, 11), _blob(2, 12)]
    labels = [0, 0, -1]
    centroids = centroids_in_original_space(vectors, labels)
    assert set(centroids) == {0}
    norm = float(np.linalg.norm(np.asarray(centroids[0])))
    assert norm == pytest.approx(1.0, abs=1e-5)
    # Mean of two members of the same tight blob stays close to both.
    sim = float(np.dot(np.asarray(vectors[0]), np.asarray(centroids[0])))
    assert sim > 0.99


def test_nearest_centroids_clamps_k_and_orders_by_similarity():
    centroids = {0: _vec(1), 1: _vec(2), 2: _vec(3), 3: _vec(4), 4: _vec(5)}
    unit = _blob(1, 99)
    top = nearest_centroids(unit, centroids, k=3)
    assert len(top) == 3
    assert top[0] == 0  # member of blob 1 -> its centroid is nearest
    # k clamped to [2, 4] and to inventory (1 centroid -> 1 result).
    assert len(nearest_centroids(unit, centroids, k=99)) == 4
    assert len(nearest_centroids(unit, {7: _vec(9)}, k=3)) == 1
    assert nearest_centroids(unit, {}, k=3) == []


def test_soft_assignment_entropy_uniform_between_two_themes():
    a = np.asarray(_vec(1))
    b = np.asarray(_vec(2))
    between = a + (b - a) / 2
    between /= np.linalg.norm(between)
    centroids = {0: [float(x) for x in a], 1: [float(x) for x in b]}
    assignments, entropy = soft_assignment_entropy([float(x) for x in between], centroids)
    assert set(assignments) == {0, 1}
    assert sum(assignments.values()) == pytest.approx(1.0, abs=1e-5)
    assert entropy == pytest.approx(math.log(2), rel=1e-3)
    assert normalized_entropy(entropy, 2) == pytest.approx(1.0, rel=1e-3)
    assert is_high_entropy(entropy, 2, threshold=0.85)


def test_soft_assignment_entropy_low_for_blob_member():
    centroids = {0: _vec(1), 1: _vec(2), 2: _vec(3)}
    member = _blob(1, 42)
    assignments, entropy = soft_assignment_entropy(member, centroids)
    # Sharp kernel: a confident member dominates its own centroid (r7 unit-
    # sphere distance fix - raw cosine distance erased the contrast). The
    # 8-d fixture vectors have a wide cosine spread, so 0.7 not 0.9.
    assert assignments[0] > 0.7
    member_h = normalized_entropy(entropy, 3)
    assert member_h < 0.8
    assert not is_high_entropy(entropy, 3, threshold=0.85)
    # And the between-theme unit from the uniform test really is higher.
    a = np.asarray(_vec(4))
    b = np.asarray(_vec(5))
    between = a + (b - a) / 2
    between /= np.linalg.norm(between)
    centroids_b = {0: [float(x) for x in a], 1: [float(x) for x in b]}
    _, entropy_between = soft_assignment_entropy([float(x) for x in between], centroids_b)
    assert normalized_entropy(entropy_between, 2) > member_h


def test_dbcv_drop_flag_tracks_twenty_percent_rule():
    assert dbcv_drop_flag(0.5, 0.39) is True  # 22% drop
    assert dbcv_drop_flag(0.5, 0.41) is False  # 18% drop
    assert dbcv_drop_flag(0.5, 0.5 * (1 - DBCV_DROP_THRESHOLD)) is False  # exactly 20%
    assert dbcv_drop_flag(None, 0.1) is False
    assert dbcv_drop_flag(0.0, 0.1) is False
    assert dbcv_drop_flag(0.5, None) is False


def test_centroid_drift_detects_moved_centroids():
    previous = {0: _vec(1)}
    assert centroid_drift(previous, {0: _vec(1)}) == pytest.approx(0.0, abs=1e-6)
    moved = centroid_drift(previous, {0: _vec(2)})
    assert moved is not None and moved > 0.1
    assert centroid_drift(previous, {9: _vec(2)}) is None


def test_suggested_label_deterministic_top_terms():
    summaries = [
        "login flow breaks on mobile devices",
        "mobile login fails for new devices",
        "the mobile app login screen freezes",
    ]
    label = suggested_label(summaries)
    assert label == "login-mobile-devices"  # ties break by first-seen order
    assert label == suggested_label(list(reversed(summaries)))


def test_inductive_config_defaults_and_overrides():
    defaults = inductive_config(None)
    assert defaults["entropy_threshold"] == 0.85
    assert defaults["recluster_every_batches"] == 8
    assert defaults["centroid_drift_threshold"] == 0.15
    assert defaults["tau_dup"] == 0.85  # config default; calibrated from gold when available
    overridden = inductive_config(
        {
            "research": {
                "inductive": {
                    "entropy_threshold": "0.9",
                    "tau_dup": 0.8,
                    "centroid_drift_threshold": "0.2",
                }
            }
        }
    )
    assert overridden["entropy_threshold"] == 0.9
    assert overridden["recluster_every_batches"] == 8
    assert overridden["centroid_drift_threshold"] == 0.2
    assert overridden["tau_dup"] == 0.8


def test_packaged_config_ships_the_inductive_governance_block():
    import pathlib

    import yaml

    for name in ("config.yaml", "src/corpus_kb/config.yaml"):
        path = pathlib.Path(__file__).resolve().parent.parent / name
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        block = cfg["research"]["inductive"]
        assert block["entropy_threshold"] == 0.85
        assert 5 <= int(block["recluster_every_batches"]) <= 10  # v5 §9.5 band
        assert 0.7 <= float(block["tau_dup"]) <= 0.95


def test_cluster_embeddings_rejects_empty():
    with pytest.raises(ValueError):
        cluster_embeddings([])


def test_summary_constants_are_the_logged_provenance():
    assert SUMMARY_PROMPT_ID == "inductive.atomic_observation.v1"
    assert SUMMARY_TEMPERATURE == 0.0
