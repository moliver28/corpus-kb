"""Multi-prototype similarity via DIY k-medoids clustering.

Why DIY over scikit-learn-extra KMedoids: on ≤50 exemplar vectors, a tight
k-medoids loop (farthest-point seeding, cosine distance, 3-5 iterations) runs
in microseconds. scikit-learn-extra adds 150 MB of dependencies. The net-new
code (this module) is a justified exception per AGENTS.md: ~50 lines pure,
testable, and faster than dependency overhead on the typical exemplar set.
"""

from __future__ import annotations

import numpy as np


def cosine_distance(v1: np.ndarray, v2: np.ndarray) -> float:
    """Cosine distance (1 - cosine similarity) between two vectors."""
    dot = np.dot(v1, v2)
    norm = np.linalg.norm(v1) * np.linalg.norm(v2)
    if norm < 1e-9:
        return 1.0
    sim = dot / norm
    return float(1.0 - max(-1.0, min(1.0, sim)))


def kmedoids_prototypes(vectors: list[list[float]], k: int = 2) -> list[list[float]]:
    """k-medoids clustering with farthest-point seeding.

    Args:
        vectors: list of embedding vectors (typically 50-100 positive examples)
        k: number of clusters, capped at len(vectors)

    Returns:
        List of k prototype vectors (medoids, each a member of the input set).
    """
    if not vectors:
        return []
    k = min(k, len(vectors))
    if k == 1:
        return [vectors[0]]

    arr = np.array(vectors, dtype=np.float32)

    # Farthest-point seeding: pick initial medoids spread out across the space
    medoids_idx = [0]
    remaining = set(range(1, len(arr)))
    for _ in range(k - 1):
        max_min_dist = -1.0
        farthest = None
        for i in remaining:
            min_dist = min(cosine_distance(arr[i], arr[m]) for m in medoids_idx)
            if min_dist > max_min_dist:
                max_min_dist = min_dist
                farthest = i
        if farthest is not None:
            medoids_idx.append(farthest)
            remaining.discard(farthest)

    # 3 iterations of k-medoids (reassign + recompute medoids)
    for _ in range(3):
        # Assign each point to nearest medoid
        clusters = [[] for _ in range(k)]
        for i in range(len(arr)):
            nearest = min(medoids_idx, key=lambda m: cosine_distance(arr[i], arr[m]))
            clusters[medoids_idx.index(nearest)].append(i)

        # Recompute medoid: index with min sum of distances to cluster members
        for c_idx, cluster in enumerate(clusters):
            if not cluster:
                continue
            best_medoid = cluster[0]
            best_cost = sum(cosine_distance(arr[best_medoid], arr[j]) for j in cluster)
            for candidate in cluster:
                cost = sum(cosine_distance(arr[candidate], arr[j]) for j in cluster)
                if cost < best_cost:
                    best_cost = cost
                    best_medoid = candidate
            medoids_idx[c_idx] = best_medoid

    return [arr[i].tolist() for i in medoids_idx]


def max_prototype_sim(vector: list[float], prototypes: list[list[float]]) -> float:
    """Maximum cosine similarity (1 - distance) to any prototype.

    Args:
        vector: embedding vector (e.g., the LLM's rationale)
        prototypes: list of prototype vectors from kmedoids_prototypes

    Returns:
        Highest cosine similarity (0.0 to 1.0, higher = more certain).
    """
    if not prototypes:
        return 0.0
    v = np.array(vector, dtype=np.float32)
    return float(max(1.0 - cosine_distance(v, np.array(p, dtype=np.float32)) for p in prototypes))
