"""Inductive clustering math (todo 15, v5 §9) — deterministic pins + entropy.

Pure numpy over caller-supplied vectors; the optional umap/hdbscan stack is
touched ONLY through the typed-Protocol shim (``_inductive_types``), so this
module imports cleanly without the extra and the clustering entry point
raises :class:`InductiveDependencyError` with the install hint.

Determinism pins (r7, recorded in every run manifest):
  * UMAP ``random_state`` and ``transform_seed`` are FIXED constants here —
    identical inputs produce identical clusters (asserted by the
    extras-installed fixture test).
  * ``n_components = 10`` for the clustering space; 2-D is display-only and
    never an engine input.
  * ``metric = 'cosine'`` on L2-normalized vectors.

Assignment-space contract (r7): soft-assignment centroids, drift snapshots,
and the Student-t entropy all live in the ORIGINAL summary-embedding space.
The UMAP-reduced space is only HDBSCAN's input — never the assignment space.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._inductive_types import HdbscanModelLike, import_hdbscan, import_umap

UMAP_RANDOM_STATE = 42
UMAP_TRANSFORM_SEED = 42
UMAP_N_NEIGHBORS = 15
CLUSTER_N_COMPONENTS = 10
DISPLAY_N_COMPONENTS = 2
METRIC = "cosine"
CLUSTER_FRACTION = 0.01
MIN_CLUSTER_SIZE_FLOOR = 5
DBCV_DROP_THRESHOLD = 0.2

ENTROPY_A = 0.02
ENTROPY_K_NEAREST_MIN = 2
ENTROPY_K_NEAREST_MAX = 4
NOISE_LABEL = -1


def min_cluster_size_for(n: int) -> int:
    """HDBSCAN ``min_cluster_size`` = max(5, ceil(0.01·N)) (v5 §9 pin)."""
    import math

    return max(MIN_CLUSTER_SIZE_FLOOR, math.ceil(CLUSTER_FRACTION * n))


def run_manifest(n_units: int) -> dict[str, object]:
    """Determinism manifest recorded with every clustering checkpoint."""
    return {
        "umap_random_state": UMAP_RANDOM_STATE,
        "umap_transform_seed": UMAP_TRANSFORM_SEED,
        "umap_n_neighbors": UMAP_N_NEIGHBORS,
        "n_components": CLUSTER_N_COMPONENTS,
        "display_n_components": DISPLAY_N_COMPONENTS,
        "metric": METRIC,
        "min_cluster_size": min_cluster_size_for(n_units),
        "min_samples": min_cluster_size_for(n_units),
        "entropy_a": ENTROPY_A,
        "dbcv_drop_threshold": DBCV_DROP_THRESHOLD,
        "noise_space": "original_union",
    }


@dataclass(frozen=True)
class ClusterResult:
    """One full HDBSCAN clustering over the reduced summary space."""

    labels: list[int]
    probabilities: list[float]
    relative_validity: float
    min_cluster_size: int
    min_samples: int


def cluster_embeddings(
    vectors: list[list[float]],
    *,
    n_components: int = CLUSTER_N_COMPONENTS,
    random_state: int = UMAP_RANDOM_STATE,
    transform_seed: int = UMAP_TRANSFORM_SEED,
) -> ClusterResult:
    """UMAP-reduce then HDBSCAN-cluster summary embeddings.

    Raises InductiveDependencyError when the ``inductive`` extra is absent —
    callers catch it and report the engine unavailable (the deductive path
    never touches this module, so it stays unaffected).
    """
    if not vectors:
        raise ValueError("cannot cluster an empty vector set")
    matrix = _unit_rows(vectors)
    n = len(matrix)
    min_cluster_size = min_cluster_size_for(n)

    umap_mod = import_umap()
    reducer = umap_mod.UMAP(
        n_neighbors=UMAP_N_NEIGHBORS,
        n_components=n_components,
        metric=METRIC,
        random_state=random_state,
        transform_seed=transform_seed,
    )
    reduced = reducer.fit_transform(matrix)

    hdbscan_mod = import_hdbscan()
    clusterer: HdbscanModelLike = hdbscan_mod.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_cluster_size,
        metric=METRIC,
        cluster_selection_method="eom",
        algorithm="generic",
        gen_min_span_tree=True,
    )
    clusterer.fit(np.asarray(reduced, dtype=np.float64))
    return ClusterResult(
        labels=[int(x) for x in clusterer.labels_.tolist()],
        probabilities=[float(x) for x in clusterer.probabilities_.tolist()],
        relative_validity=float(clusterer.relative_validity_),
        min_cluster_size=min_cluster_size,
        min_samples=min_cluster_size,
    )


def _unit_rows(vectors: list[list[float]]) -> np.ndarray:
    m = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def noise_labels_original_space(
    vectors: list[list[float]], min_cluster_size: int | None = None
) -> list[int]:
    """HDBSCAN noise labels over the ORIGINAL summary-embedding space.

    The pinned reduced-space clustering drives clusters and proposals, but the
    UMAP fuzzy graph rescues isolated points on small-N qualitative corpora
    (measured on the todo-15 fixture: every injected outlier lands within
    core distance of a theme after reduction, so the reduced fit alone never
    emits -1). The original space is already the assignment space (r7), so
    its density fit is the honest outlier reference: its -1 labels are
    UNIONED into the manual-review queue by the run layer (the queue is
    review-only and never removes a unit from its cluster).
    """
    if not vectors:
        return []
    matrix = _unit_rows(vectors)
    n = len(matrix)
    mcs = min_cluster_size if min_cluster_size is not None else min_cluster_size_for(n)
    hdbscan_mod = import_hdbscan()
    fitter: HdbscanModelLike = hdbscan_mod.HDBSCAN(
        min_cluster_size=mcs,
        min_samples=mcs,
        metric=METRIC,
        cluster_selection_method="eom",
        algorithm="generic",
    )
    fitter.fit(np.asarray(matrix, dtype=np.float64))
    return [int(x) for x in fitter.labels_.tolist()]


def centroids_in_original_space(
    vectors: list[list[float]], labels: list[int]
) -> dict[int, list[float]]:
    """Per-cluster mean centroid over ORIGINAL summary embeddings (not UMAP).

    Noise members (label -1) are excluded from centroids but never from the
    data — they land in the manual-review queue at the run layer. Centroids
    are L2-normalized (zero-norm members no-op, house contract).
    """
    matrix = _unit_rows(vectors)
    centroids: dict[int, list[float]] = {}
    for label in sorted({int(x) for x in labels} - {NOISE_LABEL}):
        members = matrix[[i for i, x in enumerate(labels) if int(x) == label]]
        mean = members.mean(axis=0)
        norm = float(np.linalg.norm(mean))
        if norm > 0:
            mean = mean / norm
        centroids[label] = [float(x) for x in mean.tolist()]
    return centroids


def _cosine(unit: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    return np.clip(centroids @ unit, -1.0, 1.0)


def clamp_k(k: int, n_centroids: int) -> int:
    """k for nearest-centroid retrieval: clamped to [2, 4] and to inventory."""
    hi = min(ENTROPY_K_NEAREST_MAX, n_centroids)
    return max(ENTROPY_K_NEAREST_MIN, min(k, hi))


def nearest_centroids(unit: list[float], centroids: dict[int, list[float]], k: int) -> list[int]:
    """Top-k centroid labels by cosine similarity (descending, stable)."""
    if not centroids:
        return []
    keys = sorted(centroids)
    matrix = np.asarray([centroids[c] for c in keys], dtype=np.float32)
    vec = _unit_rows([unit])[0]
    sims = _cosine(vec, matrix)
    k = clamp_k(k, len(keys))
    order = np.argsort(-sims, kind="stable")[:k]
    return [keys[int(i)] for i in order]


def soft_assignment_entropy(
    unit: list[float],
    centroids: dict[int, list[float]],
    k: int = 3,
    a: float = ENTROPY_A,
) -> tuple[dict[int, float], float]:
    """Student-t soft assignment over the k nearest centroids + entropy.

    v5 §9.3: ``q_ij = (1 + d_ij^2/a)^(-(a+1)/2) / sum_j'`` over the k nearest
    centroids (renormalized to the retrieved set), ``H_i = -sum_j q_ij log
    q_ij`` (category-discovery lineage). ``d_ij²`` is the SQUARED euclidean
    distance between unit-normalized vectors, ``d² = 2(1 - cos)`` — on the
    unit sphere raw cosine distance compresses toward 1 in high dimensions,
    which would erase the kernel's contrast (learned while pinning this: the
    fixture's blob members scored ~0.95 normalized entropy under raw cosine
    distance). ``a`` is the kernel sharpness pin (0.02): confident members
    land ~0.6 normalized entropy, between-theme units ~0.97. High H_i means
    the unit sits between themes (Tier-0 signal feeding todo-16's review
    priority).
    """
    nearest = nearest_centroids(unit, centroids, k)
    if not nearest:
        return {}, 0.0
    matrix = np.asarray([centroids[c] for c in nearest], dtype=np.float32)
    vec = _unit_rows([unit])[0]
    dist_sq = 2.0 * (1.0 - _cosine(vec, matrix))
    exponent = -(a + 1.0) / 2.0
    numerators = np.power(1.0 + dist_sq / a, exponent)
    denom = float(numerators.sum())
    q = numerators / denom if denom > 0 else np.full(len(nearest), 1.0 / len(nearest))
    assignments = {nearest[i]: float(q[i]) for i in range(len(nearest))}
    positive = q[q > 0]
    entropy = float(-(positive * np.log(positive)).sum())
    return assignments, entropy


def normalized_entropy(entropy: float, k: int) -> float:
    """Entropy normalized by log(k) into [0, 1] (k = assignment-set size)."""
    import math

    if k <= 1:
        return 0.0
    return entropy / math.log(k)


def is_high_entropy(entropy: float, k: int, threshold: float) -> bool:
    """Tier-0 gate: only high-entropy units earn the LLM meta-decision."""
    return normalized_entropy(entropy, k) >= threshold


def dbcv_drop_flag(
    baseline: float | None, current: float | None, threshold: float = DBCV_DROP_THRESHOLD
) -> bool:
    """>20% relative-validity drop from baseline flags re-cluster review (r10).

    A missing or non-positive baseline never flags (nothing to drop from);
    the run layer records the no-baseline state explicitly instead.
    """
    if baseline is None or current is None or baseline <= 0:
        return False
    return current < baseline * (1.0 - threshold)


def centroid_drift(
    previous: dict[int, list[float]], current: dict[int, list[float]]
) -> float | None:
    """Mean cosine distance between consecutive centroid snapshots (v5 §9.5).

    Compared over the labels present in BOTH snapshots; returns None when no
    label is shared (first snapshot or full re-shape).
    """
    shared = sorted(set(previous) & set(current))
    if not shared:
        return None
    distances = [1.0 - float(np.dot(previous[c], current[c])) for c in shared]
    return sum(distances) / len(distances)
