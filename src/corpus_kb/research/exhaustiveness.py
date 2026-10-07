"""Embedding-space exhaustiveness math (todo 17, v5 section 11 + r7 bar).

The "no missing codes/clusters" math, numpy-only and offline-testable:

* per-unit residual ``r_i = max over (code k, prototype p, view v in {A,QA})
  cos(u_i^v, proto_{k,p})`` -- the BEST any existing code can explain a unit;
* ``R(tau_res) = frac(r_i < tau_res)`` -- the share of codable units NOT
  within the calibrated residual threshold of any code (the missing-structure
  radar);
* ``tau_res`` is CALIBRATED on gold within-code similarities (G2): the P10 of
  the within-code gold cosine distribution per source type. Never hardcoded.
* coverage-curve slope across consecutive checkpoints (plateau is defined on
  R(tau_res) and/or new-codes-per-batch -- NEVER on the v5 section-8
  deductive coverage, which is threshold-driven, not a completeness measure).

Cluster re-clustering math (ARI, spherical k-means, bootstrap stability,
silhouette) lives in cluster_stability.py (250-line soft-limit split).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

import numpy as np

STABLE_ARI_THRESHOLD = 0.75
BOOTSTRAP_RESAMPLES = 10
TAU_RES_QUANTILE = 0.10
PLATEAU_EPSILON = 0.01


class ScoredUnitView(Protocol):
    """Minimal unit view the checkpoint exhaustiveness block needs.

    Read-only properties so frozen dataclasses (UnitViews) satisfy the
    Protocol structurally.
    """

    @property
    def unit_id(self) -> int: ...

    @property
    def source_type(self) -> str | None: ...


GoldView = tuple[Sequence[float], Sequence[float], Sequence[float], str]
GoldViewsByCode = Mapping[str, Sequence[GoldView]]


def unit_rows(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    """L2-normalize rows to unit length (zero rows stay zero)."""
    m = np.asarray(vectors, dtype=np.float32)
    if m.ndim == 1:
        m = m.reshape(1, -1)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def max_cos_by_id(
    vectors_by_id: Mapping[int, Sequence[float]],
    prototypes: Sequence[Sequence[float]],
) -> dict[int, float]:
    """Exact max-cosine per id over prototypes at FULL dimension (re-rank).

    The MRL analytics scans retrieve candidate id sets on the 256-d HNSW
    index; this reduction computes the FINAL values at full 1024-d so they
    stay consistent with the 1024-d tau_res calibration.
    """
    if not vectors_by_id or not prototypes:
        return {}
    matrix = unit_rows(list(vectors_by_id.values()))
    protos = unit_rows(list(prototypes))
    sims = np.clip(matrix @ protos.T, -1.0, 1.0).max(axis=1)
    return dict(zip(vectors_by_id, (float(x) for x in sims), strict=True))


def residuals(
    view_matrices: Mapping[str, Sequence[Sequence[float]]],
    prototypes_by_code: Mapping[str, Mapping[str, Sequence[Sequence[float]]]],
) -> np.ndarray:
    """Per-unit residual r_i: max cosine over codes x prototypes x views {A, QA}.

    Args:
        view_matrices: {"answer": (n, d) matrix, "qa": (n, d) matrix}.
        prototypes_by_code: {code_id: {"answer": protos, "qa": protos}}.

    Returns:
        (n,) array of max-cosine residuals in [-1, 1].
    """
    n = len(next(iter(view_matrices.values()))) if view_matrices else 0
    best = np.full(n, -1.0, dtype=np.float64)
    for view in ("answer", "qa"):
        matrix = view_matrices.get(view)
        if matrix is None or len(matrix) == 0:
            continue
        normed = unit_rows(matrix)
        for protos_by_view in prototypes_by_code.values():
            protos = protos_by_view.get(view)
            if not protos:
                continue
            sims = np.clip(normed @ unit_rows(protos).T, -1.0, 1.0)
            best = np.maximum(best, sims.max(axis=1))
    return best


def gold_within_code_similarities(
    gold_groups: Sequence[Sequence[Sequence[float]]],
) -> list[float]:
    """Pairwise within-code cosines over gold exemplar groups (G2 input)."""
    sims: list[float] = []
    for vectors in gold_groups:
        if len(vectors) < 2:
            continue
        normed = unit_rows(vectors)
        gram = np.clip(normed @ normed.T, -1.0, 1.0)
        iu = np.triu_indices(len(normed), k=1)
        sims.extend(float(x) for x in gram[iu])
    return sims


def calibrate_tau_res(
    within_sims_by_type: Mapping[str, Sequence[float]],
    quantile: float = TAU_RES_QUANTILE,
    floor: float = 0.05,
) -> dict[str, float]:
    """tau_res per source type: P(quantile) of that type's gold within-code sims.

    A unit counts as explained when its best code cosine reaches the low tail
    of what within-code gold similarity looks like for that source type. The
    threshold is therefore derived from gold geometry (G2), never hardcoded;
    a floor keeps degenerate (n<2) types from collapsing to a trivial value.
    """
    result: dict[str, float] = {}
    for source_type, sims in within_sims_by_type.items():
        if len(sims) >= 2:
            result[source_type] = float(max(floor, np.quantile(np.asarray(sims), quantile)))
        else:
            result[source_type] = floor
    return result


def percentile_bands(values: Sequence[float]) -> dict[str, float]:
    """P10 / P50 / P90 of a residual sample."""
    if not values:
        return {"p10": 0.0, "p50": 0.0, "p90": 0.0}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "p10": float(np.quantile(arr, 0.10)),
        "p50": float(np.quantile(arr, 0.50)),
        "p90": float(np.quantile(arr, 0.90)),
    }


def residual_section(
    residuals_by_type: Mapping[str, Sequence[float]],
    tau_res_by_type: Mapping[str, float],
) -> dict[str, object]:
    """R(tau_res) + P10/P50/P90 per source type (and pooled)."""
    by_type: dict[str, object] = {}
    for source_type, values in residuals_by_type.items():
        tau = float(tau_res_by_type.get(source_type, 0.0))
        bands = percentile_bands(values)
        r = (float(np.sum(np.asarray(values) < tau)) / len(values)) if values else 0.0
        by_type[source_type] = {"tau_res": tau, "R": r, "n": len(values), **bands}
    pooled: list[float] = [v for values in residuals_by_type.values() for v in values]
    tau_pool = list(tau_res_by_type.values())
    pooled_tau = float(np.mean(tau_pool)) if tau_pool else 0.0
    pooled_r = (float(np.sum(np.asarray(pooled) < pooled_tau)) / len(pooled)) if pooled else 0.0
    return {
        "by_source_type": by_type,
        "pooled": {
            "tau_res": pooled_tau,
            "R": pooled_r,
            "n": len(pooled),
            **percentile_bands(pooled),
        },
    }


def coverage_curve_slope(values: Sequence[float]) -> float:
    """OLS slope of a metric across consecutive checkpoints.

    A slope near zero on R(tau_res) (and/or on new-codes-per-batch) is the
    plateau signal; a negative slope means structure is still being absorbed.
    """
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    denom = float(np.sum((x - x.mean()) ** 2))
    if denom == 0.0:
        return 0.0
    return float(np.sum((x - x.mean()) * (y - y.mean())) / denom)


def plateau_detected(slope: float, epsilon: float = PLATEAU_EPSILON) -> bool:
    """Plateau when the checkpoint-to-checkpoint slope is ~flat."""
    return abs(slope) <= epsilon


def exhaustiveness_block(
    units: Sequence[ScoredUnitView],
    scored: Mapping[str, Mapping[str, Sequence[float]]],
    gold_by_code: GoldViewsByCode,
) -> dict[str, object]:
    """Residual R(tau_res) + percentiles per source type (checkpoint payload).

    tau_res is calibrated per source type from gold within-code answer-view
    similarities (G2); the residual is the max over codes of max(s_a, s_qa) —
    both computed from matrices the deductive run already scored (no extra
    vector IO). The governance report reads this block as its coverage-curve
    prior-R input.
    """
    if not scored or not units:
        return {}
    within_sims_by_type: dict[str, list[float]] = {}
    for gold in gold_by_code.values():
        by_type: dict[str, list[list[float]]] = {}
        for g in gold:
            if g[0]:
                by_type.setdefault(g[3], []).append(list(g[0]))
        for source_type, vectors in by_type.items():
            if len(vectors) >= 2:
                within_sims_by_type.setdefault(source_type, []).extend(
                    gold_within_code_similarities([vectors])
                )
    tau_res = calibrate_tau_res(within_sims_by_type)
    residuals_by_type: dict[str, list[float]] = {}
    for i, unit in enumerate(units):
        r = max(max(scored[c]["answer"][i], scored[c]["qa"][i]) for c in scored)
        residuals_by_type.setdefault(unit.source_type or "unknown", []).append(r)
    return residual_section(residuals_by_type, tau_res)
