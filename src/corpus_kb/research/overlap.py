"""Semantic code overlap (todo 17, v5 §11; Oracle A D2 + Momus M-3).

The code-centroid matrix ``M = C_hat C_hat^T`` over L2-normalized code
centroids in the DEDUCTIVE unit space (answer-view embedding_256 means over
each code's assigned units — computed SQL-side by the caller, see
analytics_sql.code_centroids_256; the k x k product itself is trivially
small, not a kNN scan). Pairs with cosine above the CALIBRATED ``tau_overlap``
are flagged for codebook review, alongside the shared-unit confusion matrix
(units holding both codes, and units whose top-2 margin < ``delta_amb``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

import numpy as np

TAU_OVERLAP_FLOOR = 0.70
TAU_OVERLAP_CEIL = 0.95
TAU_OVERLAP_MARGIN = 0.05


def _row(v: Sequence[float]) -> np.ndarray:
    arr = np.asarray(v, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    return arr / norm if norm > 0 else arr


def code_centroid_matrix(centroids: Sequence[Sequence[float]]) -> np.ndarray:
    """M = C_hat C_hat^T for L2-normalized centroids (k x k cosine matrix)."""
    if not centroids:
        return np.zeros((0, 0), dtype=np.float32)
    normed = np.vstack([_row(c) for c in centroids])
    return np.clip(normed @ normed.T, -1.0, 1.0)


def calibrate_tau_overlap(
    gold_inter_code_max: float | None,
    margin: float = TAU_OVERLAP_MARGIN,
    floor: float = TAU_OVERLAP_FLOOR,
    ceiling: float = TAU_OVERLAP_CEIL,
) -> float:
    """tau_overlap from gold separation: max inter-code gold cosine + margin.

    Same calibration policy as promote_code.calibrate_tau_dup: with gold
    codes, anything above the WORST observed between-code gold separation is
    overlap at run time (run centroids drift closer than gold geometry).
    Clamped to [floor, ceiling]; falls back to the floor with no gold.
    """
    if gold_inter_code_max is None:
        return floor
    return float(min(ceiling, max(floor, float(gold_inter_code_max) + margin)))


def flag_overlap_pairs(
    matrix: np.ndarray, code_ids: Sequence[str], tau_overlap: float
) -> list[dict[str, object]]:
    """Off-diagonal pairs with cosine > tau_overlap (worst first)."""
    k = len(code_ids)
    if matrix.shape != (k, k):
        raise ValueError("matrix must be k x k for code_ids")
    flags: list[dict[str, object]] = []
    for i in range(k):
        for j in range(i + 1, k):
            cos = float(matrix[i, j])
            if cos > tau_overlap:
                flags.append({"a": code_ids[i], "b": code_ids[j], "cos": cos})
    flags.sort(key=lambda f: -float(cast("float", f["cos"])))
    return flags


def shared_unit_confusion(
    members_by_code: Mapping[str, set[int]],
    margins: Mapping[int, float] | None = None,
    delta_amb: float = 0.05,
) -> list[dict[str, object]]:
    """Per-pair shared-unit confusion counts.

    For every code pair: ``both_codes`` counts units assigned to BOTH codes;
    ``ambiguous`` counts units whose top-1/top-2 assignment margin < delta_amb
    with exactly that top-2 pair (when margins are supplied). ``combined``
    de-duplicates the union.
    """
    codes = sorted(members_by_code)
    result: list[dict[str, object]] = []
    for i, a in enumerate(codes):
        for b in codes[i + 1 :]:
            both = members_by_code[a] & members_by_code[b]
            ambiguous: set[int] = set()
            if margins:
                for unit_id, margin in margins.items():
                    if margin < delta_amb and unit_id in both:
                        ambiguous.add(unit_id)
            result.append(
                {
                    "a": a,
                    "b": b,
                    "both_codes": len(both),
                    "ambiguous_margin": len(ambiguous),
                    "combined": len(both | ambiguous),
                }
            )
    return result
