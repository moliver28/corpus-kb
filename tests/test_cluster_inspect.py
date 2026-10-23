"""Unit tests for clusters_inspect.

The reduction seam is faked for the structure tests (fast, no numba);
one test drives the REAL reduction + clustering path (PCA fallback +
sklearn HDBSCAN — umap is not installed in this venv) and asserts
STRUCTURAL stability only: same data, same cluster sizes and noise
count on one machine. Coordinate byte-equality is never asserted (the
AMR rule: UMAP/PCA coordinates are not a contract; structure is).
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from corpus_kb.analysis import vectors
from corpus_kb.errors import DependencyMissingError, ValidationError
from corpus_kb.tools import cluster_tools

try:  # umap is optional; the real-UMAP test skips without it
    import umap  # noqa: F401

    HAS_UMAP = True
except ImportError:
    HAS_UMAP = False

# ---------------------------------------------------------------------------
# Fakes


def _space(rows: dict[str, list[float]]) -> vectors.VectorSpace:
    ids = sorted(rows)
    return vectors.VectorSpace(
        ids=ids,
        matrix=np.asarray([rows[i] for i in ids], dtype=np.float64),
        excluded_zero_count=0,
    )


def _patch(monkeypatch: pytest.MonkeyPatch, space: vectors.VectorSpace) -> None:
    async def fake_vectors(pg_pool: object, tenant_id: str, config: object) -> vectors.VectorSpace:
        return space

    monkeypatch.setattr(cluster_tools, "_load_vectors", fake_vectors)


TWO_BLOBS: dict[str, list[float]] = {
    # Two tight 3-point blobs plus two far singletons: sklearn HDBSCAN
    # (eom, min_cluster_size=3) reports sizes [3, 3] and noise 2 here.
    "a1": [0.0, 0.0],
    "a2": [0.01, 0.0],
    "a3": [0.0, 0.01],
    "b1": [10.0, 0.0],
    "b2": [10.01, 0.0],
    "b3": [10.0, 0.01],
    "n1": [50.0, 50.0],
    "n2": [-50.0, -50.0],
}


def _fake_reduce(matrix: np.ndarray) -> tuple[np.ndarray, str]:
    """Identity-ish reduction seam: pass the 2-D layout straight through."""
    return matrix[:, :2], "fake"


# ---------------------------------------------------------------------------
# Envelope structure (fake reduction, real sklearn HDBSCAN)


async def test_clusters_inspect_reports_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _space(TWO_BLOBS))
    monkeypatch.setattr(cluster_tools, "_reduce", _fake_reduce)
    out = await cluster_tools.clusters_inspect(None, None, "t", min_cluster_size=3)
    assert out["status"] == "success"
    assert out["screened_count"] == 8
    sizes = [cluster["size"] for cluster in out["clusters"]]
    assert sizes == sorted(sizes, reverse=True)
    assert sum(sizes) + out["noise_count"] == 8
    # The dense 3-point blobs survive min_cluster_size=3; the sparse
    # middle pair cannot form a cluster and lands in noise.
    assert sizes == [3, 3]
    assert out["noise_count"] == 2
    assert out["params"]["reduction"] == "fake"
    for cluster in out["clusters"]:
        assert len(cluster["exemplar_chunk_ids"]) <= 3
        assert cluster["cluster_id"] >= 0


async def test_clusters_inspect_is_structurally_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _space(TWO_BLOBS))
    monkeypatch.setattr(cluster_tools, "_reduce", _fake_reduce)
    first = await cluster_tools.clusters_inspect(None, None, "t", min_cluster_size=3)
    second = await cluster_tools.clusters_inspect(None, None, "t", min_cluster_size=3)
    assert first["clusters"] == second["clusters"]
    assert first["noise_count"] == second["noise_count"]


async def test_clusters_inspect_exemplars_come_from_one_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _space(TWO_BLOBS))
    monkeypatch.setattr(cluster_tools, "_reduce", _fake_reduce)
    out = await cluster_tools.clusters_inspect(None, None, "t", min_cluster_size=3)
    stems = []
    for cluster in out["clusters"]:
        exemplar_stems = {exemplar[0] for exemplar in cluster["exemplar_chunk_ids"]}
        assert len(exemplar_stems) == 1  # a cluster's exemplars share its blob stem
        stems.append(exemplar_stems.pop())
    assert len(set(stems)) == 2  # the two clusters are different blobs


# ---------------------------------------------------------------------------
# Real reduction path: PCA fallback (umap absent) + sklearn HDBSCAN


def _blob_rows() -> dict[str, list[float]]:
    rng = np.random.default_rng(42)
    rows: dict[str, list[float]] = {}
    centers = [([5.0] * 8, "a"), ([-5.0] * 8, "b"), ([0.0] * 7 + [9.0], "c")]
    index = 0
    for center, stem in centers:
        for _ in range(6):
            point = np.asarray(center) + rng.normal(0.0, 0.2, 8)
            rows[f"{stem}{index:02d}"] = [float(v) for v in point]
            index += 1
    return rows


async def test_real_path_structural_stability(monkeypatch: pytest.MonkeyPatch) -> None:
    if HAS_UMAP:  # the fallback name differs; keep this test on the fallback
        pytest.skip("umap installed; the PCA-fallback path is not taken")
    _patch(monkeypatch, _space(_blob_rows()))
    first = await cluster_tools.clusters_inspect(None, None, "t", min_cluster_size=3)
    second = await cluster_tools.clusters_inspect(None, None, "t", min_cluster_size=3)
    assert first["status"] == "success"
    assert first["params"]["reduction"] == "pca_fallback"
    assert first["params"]["random_state"] == 42
    # Structure is the contract; coordinates are not.
    assert [c["size"] for c in first["clusters"]] == [c["size"] for c in second["clusters"]]
    assert first["noise_count"] == second["noise_count"]
    assert sum(c["size"] for c in first["clusters"]) + first["noise_count"] == 18


async def test_real_umap_path_reports_umap(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_UMAP:
        pytest.skip("umap not installed; the UMAP path cannot run here")
    _patch(monkeypatch, _space(_blob_rows()))
    out = await cluster_tools.clusters_inspect(None, None, "t", min_cluster_size=3)
    assert out["status"] == "success"
    assert out["params"]["reduction"] == "umap"


# ---------------------------------------------------------------------------
# Honest edges and validation


async def test_clusters_inspect_empty_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _space({}))
    out = await cluster_tools.clusters_inspect(None, None, "t")
    assert out["status"] == "empty"
    assert out["reason"] and out["remedy"]
    assert out["clusters"] == []
    assert out["screened_count"] == 0


async def test_clusters_inspect_single_chunk_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _space({"solo": [1.0, 0.0]}))
    out = await cluster_tools.clusters_inspect(None, None, "t")
    assert out["status"] == "empty"
    assert out["screened_count"] == 1


async def test_clusters_inspect_sample_cap_bounds_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space = _space({f"p{i}": [float(i), float(i % 3)] for i in range(8)})
    _patch(monkeypatch, space)
    monkeypatch.setattr(cluster_tools, "_reduce", _fake_reduce)
    out = await cluster_tools.clusters_inspect(None, None, "t", sample_cap=4)
    assert out["screened_count"] == 4
    again = await cluster_tools.clusters_inspect(None, None, "t", sample_cap=4)
    assert out["clusters"] == again["clusters"]  # seeded sample, stable


async def test_clusters_inspect_rejects_bad_params(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _space(TWO_BLOBS))
    with pytest.raises(ValidationError):
        await cluster_tools.clusters_inspect(None, None, "t", min_cluster_size=1)
    with pytest.raises(ValidationError):
        await cluster_tools.clusters_inspect(None, None, "t", sample_cap=0)


async def test_clusters_inspect_answers_the_typed_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _space(TWO_BLOBS))
    monkeypatch.setitem(sys.modules, "sklearn", None)
    monkeypatch.setitem(sys.modules, "sklearn.cluster", None)
    with pytest.raises(DependencyMissingError):
        await cluster_tools.clusters_inspect(None, None, "t")
