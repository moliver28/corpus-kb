from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def bounded_floor(pos_pctile: float, neg_pctile: float) -> float:
    """
    Bounded empirical floor combining recall (positive percentile) and
    precision (negative percentile).

    Returns the maximum of the two percentiles, ensuring that neither metric
    undermines the floor.

    Args:
        pos_pctile: Positive (target) percentile value (e.g., 2nd percentile).
        neg_pctile: Negative (non-target) percentile value (e.g., 94th percentile).

    Returns:
        max(pos_pctile, neg_pctile)
    """
    return max(pos_pctile, neg_pctile)


def calibrate(
    pos_cosines: Sequence[float],
    neg_cosines: Sequence[float],
    min_pos: int = 10,
    global_fallback: float | None = None,
) -> tuple[float, float]:
    """
    Calibrate pool and residual floors from empirical cosine similarity distributions.

    Pool floor uses 2nd/94th percentiles.
    Residual floor uses 5th/97th percentiles.
    Falls back to global_fallback (typically a global median) when insufficient positive cosines.

    Args:
        pos_cosines: Cosine similarities for positive (target) matches.
        neg_cosines: Cosine similarities for negative (non-target) matches.
        min_pos: Minimum positive cosines required to compute floor (default 10).
        global_fallback: Fallback value when len(pos_cosines) < min_pos.

    Returns:
        (pool_floor, residual_floor) tuple of floats.
    """
    pos_arr = np.array(pos_cosines) if pos_cosines else np.array([])
    neg_arr = np.array(neg_cosines) if neg_cosines else np.array([])

    # If insufficient positive samples, return global fallback
    if len(pos_arr) < min_pos:
        fallback = global_fallback if global_fallback is not None else 0.0
        return (fallback, fallback)

    # Pool floor: 2nd percentile of positives, 94th percentile of negatives
    pool_pos_pctile = float(np.percentile(pos_arr, 2)) if len(pos_arr) > 0 else 0.0
    pool_neg_pctile = float(np.percentile(neg_arr, 94)) if len(neg_arr) > 0 else 0.0
    pool_floor = bounded_floor(pool_pos_pctile, pool_neg_pctile)

    # Residual floor: 5th percentile of positives, 97th percentile of negatives
    residual_pos_pctile = float(np.percentile(pos_arr, 5)) if len(pos_arr) > 0 else 0.0
    residual_neg_pctile = float(np.percentile(neg_arr, 97)) if len(neg_arr) > 0 else 0.0
    residual_floor = bounded_floor(residual_pos_pctile, residual_neg_pctile)

    return (pool_floor, residual_floor)
