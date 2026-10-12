"""Cluster stability + grouping math (todo 17, v5 §11).

Split out of exhaustiveness.py to honor the 250-line soft limit: the
residual/tau_res math lives there; THIS module owns re-clustering —

* Adjusted Rand Index (contingency form; noise label -1 is its own group);
* deterministic spherical k-means (numpy only — the fallback clusterer for
  stability and candidate-missing-code grouping; callers with the inductive
  extra pass the engine's HDBSCAN pipeline instead);
* bootstrap stability: >=10 resamples, mean pairwise ARI (>=0.75 stable);
* per-group mean silhouette (cosine space) for the overlap section;
* top-term Jaccard content stability (U16): mean pairwise Jaccard of the
  top-K term sets across seed/bootstrap runs, reported SEPARATELY from the
  membership ARI (v6 §7 U16: gate on content stability, report membership
  stability beside it).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

import numpy as np

from corpus_kb.research.exhaustiveness import BOOTSTRAP_RESAMPLES, STABLE_ARI_THRESHOLD, unit_rows

T = TypeVar("T")


def ari(labels_a: Sequence[T], labels_b: Sequence[T]) -> float:
    """Adjusted Rand Index (contingency form; noise label -1 is its own group).

    Returns 1.0 for identical partitions, ~0.0 for chance agreement.
    """
    if len(labels_a) != len(labels_b):
        raise ValueError("label vectors must align")
    n = len(labels_a)
    if n < 2:
        return 1.0
    a = np.asarray([str(x) for x in labels_a])
    b = np.asarray([str(x) for x in labels_b])
    ua = np.unique(a)
    ub = np.unique(b)
    cont = np.zeros((len(ua), len(ub)), dtype=np.int64)
    for i, ca in enumerate(ua):
        for j, cb in enumerate(ub):
            cont[i, j] = int(np.sum((a == ca) & (b == cb)))

    def comb2(x: np.ndarray) -> np.ndarray:
        return x.astype(np.float64) * (x.astype(np.float64) - 1.0) / 2.0

    sum_c = float(np.sum(comb2(cont)))
    sum_a = float(np.sum(comb2(np.array([int(np.sum(a == ca)) for ca in ua]))))
    sum_b = float(np.sum(comb2(np.array([int(np.sum(b == cb)) for cb in ub]))))
    total = n * (n - 1) / 2.0
    expected = sum_a * sum_b / total
    max_index = (sum_a + sum_b) / 2.0
    if max_index == expected:
        return 1.0
    return (sum_c - expected) / (max_index - expected)


def spherical_kmeans(
    vectors: Sequence[Sequence[float]], k: int, seed: int = 42, max_iter: int = 50
) -> np.ndarray:
    """Deterministic spherical k-means (numpy only): max-cos assignment,
    mean-then-renormalize updates, seeded k-means++-style init."""
    normed = unit_rows(vectors)
    n = len(normed)
    k = max(1, min(int(k), n))
    rng = np.random.default_rng(seed)
    centers = [int(rng.integers(n))]
    while len(centers) < k:
        dists = np.clip(np.min(1.0 - normed @ normed[centers].T, axis=1), 0.0, None)
        total = float(dists.sum())
        pick = n - 1 if total <= 0 else int(rng.choice(n, p=dists / total))
        centers.append(pick)
    center_matrix = normed[centers].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        sims = normed @ center_matrix.T
        new_labels = np.argmax(sims, axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        for c in range(k):
            members = normed[labels == c]
            if len(members) == 0:
                continue
            mean = members.mean(axis=0)
            norm = np.linalg.norm(mean)
            center_matrix[c] = mean / norm if norm > 0 else center_matrix[c]
    return labels


def bootstrap_stability(
    vectors: Sequence[Sequence[float]],
    cluster_fn: Callable[[np.ndarray], Sequence[int]] | None = None,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    subsample_frac: float = 0.8,
    seed: int = 42,
) -> dict[str, object]:
    """Mean pairwise ARI across >=10 bootstrap resamples (>=0.75 = stable).

    Each resample draws a subsample (without replacement, the standard
    cluster-stability assessment) and re-clusters it with ``cluster_fn``
    (default: deterministic spherical k-means, k=3; callers with the
    inductive extra pass the engine's HDBSCAN pipeline instead). Pairwise
    ARI is computed over the shared points of each resample pair.
    """
    fn = cluster_fn or (lambda m: spherical_kmeans(m, 3, seed=seed))
    n = len(vectors)
    if n < 4:
        return {
            "n_resamples": 0,
            "mean_pairwise_ari": None,
            "threshold": STABLE_ARI_THRESHOLD,
            "stable": None,
            "note": "fewer than 4 units; stability undefined",
        }
    m = max(2, round(subsample_frac * n))
    rng = np.random.default_rng(seed)
    samples: list[tuple[np.ndarray, np.ndarray]] = []
    for _ in range(n_resamples):
        idx = np.sort(rng.choice(n, size=m, replace=False))
        clustered = fn(unit_rows(np.asarray(vectors)[idx].tolist()))
        samples.append((idx, np.asarray(clustered, dtype=int)))
    pair_aris: list[float] = []
    for i in range(n_resamples):
        for j in range(i + 1, n_resamples):
            idx_i, lab_i = samples[i]
            idx_j, lab_j = samples[j]
            _, common_i, common_j = np.intersect1d(idx_i, idx_j, return_indices=True)
            if len(common_i) < 2:
                continue
            pair_aris.append(ari(lab_i[common_i].tolist(), lab_j[common_j].tolist()))
    mean_ari = float(np.mean(pair_aris)) if pair_aris else None
    return {
        "n_resamples": n_resamples,
        "mean_pairwise_ari": mean_ari,
        "threshold": STABLE_ARI_THRESHOLD,
        "stable": None if mean_ari is None else bool(mean_ari >= STABLE_ARI_THRESHOLD),
    }


def silhouette_by_group(
    vectors: Sequence[Sequence[float]], labels: Sequence[int]
) -> dict[int, float]:
    """Per-group mean silhouette (cosine distance). Singleton groups score 0.

    Noise labels (-1) are excluded from both the scoring and the neighbor
    pools, matching the cluster sections' convention.
    """
    normed = unit_rows(vectors)
    labels_arr = np.asarray(labels, dtype=int)
    keep = labels_arr != -1
    normed = normed[keep]
    labels_arr = labels_arr[keep]
    n = len(labels_arr)
    result: dict[int, float] = {}
    if n < 2:
        return result
    dist = 1.0 - np.clip(normed @ normed.T, -1.0, 1.0)
    for label in sorted({int(x) for x in labels_arr}):
        members = np.where(labels_arr == label)[0]
        if len(members) <= 1:
            result[label] = 0.0
            continue
        scores: list[float] = []
        for i in members:
            a = float(np.mean(dist[i, members])) if len(members) > 1 else 0.0
            others = [
                np.where(labels_arr == other)[0]
                for other in set(labels_arr.tolist())
                if other != label
            ]
            if not others or all(len(o) == 0 for o in others):
                scores.append(0.0)
                continue
            b = min(float(np.mean(dist[i, o])) for o in others if len(o) > 0)
            denom = max(a, b)
            scores.append((b - a) / denom if denom > 0 else 0.0)
        result[label] = float(np.mean(scores))
    return result


@dataclass(frozen=True)
class TopTermStability:
    """Content stability of clustering runs by top-term overlap (U16)."""

    mean_jaccard: float | None  # None when fewer than 2 runs
    n_runs: int
    k: int
    threshold: float


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def top_term_jaccard(
    runs: Sequence[Sequence[str]],
    k: int = 10,
    threshold: float = 0.7,
) -> TopTermStability:
    """Mean pairwise Jaccard of top-K term sets across runs (U16).

    Args:
        runs: One ranked top-terms list per clustering run (e.g. the
            keyness top terms of each seed/bootstrap re-run), best first.
        k: Top-K truncation per run before the set comparison.
        threshold: Content-stability floor the gate compares against
            (membership ARI is reported separately by
            ``bootstrap_stability``; this metric gates CONTENT stability).

    Returns:
        TopTermStability. Fewer than 2 runs -> mean_jaccard None (honest
        not-evaluable upstream), never a fabricated 1.0.
    """
    if len(runs) < 2:
        return TopTermStability(mean_jaccard=None, n_runs=len(runs), k=k, threshold=threshold)
    top_sets = [frozenset(run[:k]) for run in runs]
    pair_jaccards: list[float] = []
    for i in range(len(top_sets)):
        for j in range(i + 1, len(top_sets)):
            pair_jaccards.append(_jaccard(top_sets[i], top_sets[j]))
    return TopTermStability(
        mean_jaccard=float(np.mean(pair_jaccards)),
        n_runs=len(runs),
        k=k,
        threshold=threshold,
    )
