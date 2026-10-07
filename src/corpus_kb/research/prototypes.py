"""K-medoids / PAM prototype selection over gold exemplars (todo 14, v5 §8).

* NO scipy — pure numpy.
* Deterministic: BUILD init + best-improvement SWAP; caller passes a seeded
  Generator for any tie-breaking or stochastic seeding.
* Multi-prototype per code: prototypes are chosen independently per view
  (answer / QA / question) from the gold exemplars for that code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

import numpy as np


def _unit_rows(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    """Normalize rows to unit length (zero rows stay zero)."""
    m = np.array(vectors, dtype=np.float32)
    if m.ndim != 2:
        raise ValueError("vectors must be a sequence of same-length vectors")
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def _cosine_distance_matrix(vectors: np.ndarray) -> np.ndarray:
    """Pairwise cosine distance for unit-normalized rows.

    Returns a clipped distance matrix in [0, 1].
    """
    sim = np.clip(vectors @ vectors.T, -1.0, 1.0)
    return 1.0 - sim


def _total_cost(distances: np.ndarray, medoids: set[int]) -> float:
    """Sum of min distances from each point to its nearest medoid."""
    if not medoids:
        return float(np.sum(distances))  # pragma: no cover
    medoid_idx = sorted(medoids)
    return float(np.sum(np.min(distances[:, medoid_idx], axis=1)))


def k_medoids(
    vectors: Sequence[Sequence[float]],
    k: int,
    *,
    max_swarms: int = 100,
    rng: np.random.Generator | None = None,
) -> list[list[float]]:
    """Run PAM k-medoids and return the k prototype vectors.

    Deterministic when ``rng`` is seeded. The algorithm:
      1. BUILD: greedily add the medoid that most reduces total cost.
      2. SWAP: iteratively swap a medoid with a non-medoid if total cost
         improves; the best improvement is chosen each pass.

    Args:
        vectors: List of n vectors of equal dimension.
        k: Number of medoids (1 <= k <= n).
        max_swarms: Maximum SWAP passes.
        rng: Optional seeded numpy Generator for any future stochastic
            initialization. The current BUILD init is deterministic, but
            callers should pass a seeded generator for reproducibility.

    Returns:
        The k medoid vectors as plain Python lists.
    """
    del rng  # deterministic BUILD; keep in signature for reproducibility contract
    normed = _unit_rows(vectors)
    n = len(normed)
    if n == 0:
        raise ValueError("cannot build prototypes from empty vector list")
    k = int(k)
    if k < 1 or k > n:
        raise ValueError(f"k must be in [1, {n}], got {k}")
    if k == n:
        return [v.tolist() for v in normed]

    distances = _cosine_distance_matrix(normed)

    # BUILD: first medoid minimizes sum of distances to all points.
    first = int(np.argmin(np.sum(distances, axis=1)))
    medoids: set[int] = {first}

    while len(medoids) < k:
        best_candidate: int | None = None
        best_gain = -1.0
        current_cost = _total_cost(distances, medoids)
        for cand in range(n):
            if cand in medoids:
                continue
            trial = medoids | {cand}
            gain = current_cost - _total_cost(distances, trial)
            if gain > best_gain or (
                gain == best_gain and (best_candidate is None or cand < best_candidate)
            ):
                best_gain = gain
                best_candidate = cand
        if best_candidate is None:
            break
        medoids.add(best_candidate)

    # SWAP: best-improvement exchanges.
    non_medoids = [i for i in range(n) if i not in medoids]
    for _ in range(max_swarms):
        best_swap: tuple[int, int] | None = None
        best_delta = 0.0
        current_cost = _total_cost(distances, medoids)
        for m in sorted(medoids):
            trial_without = medoids - {m}
            for o in non_medoids:
                trial = trial_without | {o}
                delta = _total_cost(distances, trial) - current_cost
                if delta < best_delta:
                    best_delta = delta
                    best_swap = (m, o)
        if best_swap is None:
            break
        old, new = best_swap
        medoids = (medoids - {old}) | {new}
        non_medoids = [i for i in range(n) if i not in medoids]

    return [normed[i].tolist() for i in sorted(medoids)]


def choose_k(n_positives: int, max_prototypes: int = 5) -> int:
    """Number of prototypes for a code with n_positives gold exemplars.

    Caps at ``max_prototypes`` and never requests more medoids than points.
    """
    if n_positives <= 0:
        return 0
    return min(max_prototypes, max(1, n_positives))


def build_view_prototypes(
    gold_vectors: Sequence[Sequence[float]],
    max_prototypes: int = 5,
    rng: np.random.Generator | None = None,
) -> list[list[float]]:
    """Build multi-prototype vectors for one code/view from gold exemplars.

    Args:
        gold_vectors: Unit vectors for every gold-positive exemplar in this view.
        max_prototypes: Upper bound on number of prototypes.
        rng: Seeded numpy Generator for reproducibility.

    Returns:
        List of prototype vectors (each unit-normalized).
    """
    k = choose_k(len(gold_vectors), max_prototypes)
    if k == 0:
        return []
    return k_medoids(gold_vectors, k, rng=rng)


ViewPrototypes = dict[str, list[list[float]]]


def build_code_prototypes(
    gold_by_code: Mapping[str, Mapping[str, Sequence[Sequence[float]]]],
    max_prototypes: int = 5,
    rng: np.random.Generator | None = None,
) -> dict[str, ViewPrototypes]:
    """Build per-view prototypes for every code in ``gold_by_code``.

    Args:
        gold_by_code: {code_id: {"answer": [...], "qa": [...], "question": [...]}}.
        max_prototypes: Upper bound on prototypes per code/view.
        rng: Seeded numpy Generator for reproducibility.

    Returns:
        {code_id: {"answer": prototypes, "qa": prototypes, "question": prototypes}}.
    """
    result: dict[str, ViewPrototypes] = {}
    for code_id, views in gold_by_code.items():
        result[code_id] = {
            view: build_view_prototypes(cast(Sequence[Sequence[float]], vecs), max_prototypes, rng)
            for view, vecs in views.items()
        }
    return result
