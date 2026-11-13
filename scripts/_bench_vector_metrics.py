"""Deterministic measurement math for the vector-type benchmark.

Seeded corpus/queries, exact cosine top-k, recall@k, latency percentiles,
the MRL-256 slice, and the U46 halfvec adoption gate. Pure numpy plus the
repo HashRandom; no database imports (the DB harness lives in
bench_vector_types.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from corpus_kb.utils.deterministic import HashRandom

# U46 gate constants: admin tolerance (recall points) + material-gain bar.
RECALL_TOLERANCE_POINTS = 1.0
MIN_MATERIAL_GAIN_RATIO = 0.25


@dataclass
class Decision:
    """U46 halfvec adoption gate verdict (admin decides; this informs)."""

    decision: str
    reasons: list[str] = field(default_factory=list)


def adoption_decision(
    recall_loss_points: float,
    size_reduction_ratio: float,
    build_time_reduction_ratio: float,
    recall_tolerance_points: float = RECALL_TOLERANCE_POINTS,
    min_material_gain: float = MIN_MATERIAL_GAIN_RATIO,
) -> Decision:
    """U46 gate: recall loss <= tolerance AND a material size/build gain.

    ``*_reduction_ratio`` are fractions (0.4 = 40% smaller/faster). Never
    adopts on recall alone; never adopts with a recall loss over tolerance.
    """
    reasons: list[str] = []
    recall_ok = recall_loss_points <= recall_tolerance_points
    material = (
        size_reduction_ratio >= min_material_gain or build_time_reduction_ratio >= min_material_gain
    )
    if not recall_ok:
        reasons.append(
            f"recall loss {recall_loss_points:.2f}pt exceeds tolerance "
            f"{recall_tolerance_points:.2f}pt"
        )
    if not material:
        reasons.append(
            f"gains not material: size {size_reduction_ratio:.0%}, "
            f"build {build_time_reduction_ratio:.0%} < {min_material_gain:.0%}"
        )
    if recall_ok and material:
        reasons.append("recall within tolerance and gains are material")
    return Decision(decision="adopt" if (recall_ok and material) else "keep", reasons=reasons)


def seeded_unit_vectors(rows: int, dims: int, seed: int) -> np.ndarray:
    """Deterministic row-normalized matrix (the ground-truth corpus)."""
    rng = np.random.default_rng(seed)
    vectors = rng.standard_normal((rows, dims)).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def seeded_queries(vectors: np.ndarray, probes: int, seed: int) -> np.ndarray:
    """Queries drawn from the corpus itself (guaranteed nearest neighbors)."""
    rng = HashRandom(seed)
    picks = sorted(rng.sample(range(vectors.shape[0]), min(probes, vectors.shape[0])))
    return vectors[picks]


def exact_topk(matrix: np.ndarray, query: np.ndarray, k: int) -> list[int]:
    """Exact cosine top-k ids (rows are unit vectors, so dot == cosine)."""
    sims = matrix @ query
    return [int(i) for i in np.argsort(-sims)[:k]]


def recall_at_k(ann_ids: list[int], exact_ids: list[int]) -> float:
    if not exact_ids:
        return 0.0
    return len(set(ann_ids) & set(exact_ids)) / len(set(exact_ids))


def latency_percentiles(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"p50_ms": 0.0, "p95_ms": 0.0}
    arr = np.asarray(samples_ms, dtype=np.float64)
    return {"p50_ms": float(np.percentile(arr, 50)), "p95_ms": float(np.percentile(arr, 95))}


def _vector_literal(vector: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.9g}" for x in vector) + "]"


def _mrl_256(vector: np.ndarray) -> np.ndarray:
    slice_ = vector[:256]
    norm = float(np.linalg.norm(slice_))
    return slice_ / norm if norm > 0 else slice_
