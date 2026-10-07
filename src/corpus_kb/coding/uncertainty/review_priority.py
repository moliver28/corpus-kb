"""Combined review priority + ambiguity routing (todo 16, v5 §10).

``priority = w0*(1-margin) + w1*H_tok + w2*SE + w3*hedge + w4*interpretive
+ w5*(1-link_score) + w6*question_dependent`` computed on RANK-NORMALIZED
signals (equal weights are the DEFAULT; an L2-regularized logistic refit
against reviewer overrides requires >= 300 reviewed items — Oracle A r7).

Top-2-margin ambiguity routing: units whose top1-top2 code-score margin is
below the calibrated ``delta_amb`` route with reason ``overlap`` (r7).

REASON ENUM is CLOSED (r11): ``overlap | conformal | interpretive |
gray_zone``. Raw decision reasons from the deductive layer are mapped onto
the enum at the routing boundary; the conformal set-size stays ROUTING-ONLY
(never an 8th priority signal — it would double-count the margin w0, r11).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

MIN_ITEMS_FOR_REFIT = 300

# Closed review-routing reason enum (r11). Asserted in fixtures.
REVIEW_REASONS: frozenset[str] = frozenset({"overlap", "conformal", "interpretive", "gray_zone"})

# w0..w6 in v5 §10 order.
WEIGHT_KEYS = (
    "margin",
    "h_tok",
    "se",
    "hedge",
    "interpretive",
    "link_score",
    "question_dependent",
)


class InsufficientReviewDataError(ValueError):
    """Refit attempted below the >=300 reviewed-items bar (Oracle A r7)."""


def default_weights() -> dict[str, float]:
    """Equal weights — the DEFAULT until a random audit validates each signal."""
    return dict.fromkeys(WEIGHT_KEYS, 1.0)


@dataclass(frozen=True)
class ReviewSignal:
    """Raw (pre-normalization) per-unit review signals.

    None means NOT MEASURED (tier-1 logprobs unavailable; tier-3 not
    escalated; no exchange link). A missing signal contributes its lowest
    rank: the priority never fabricates uncertainty that was not measured.
    """

    margin: float
    h_tok: float | None = None
    se: float | None = None
    hedge: int = 0
    interpretive: bool = False
    link_score: float | None = None
    question_dependent: bool = False


def rank_normalize(values: Sequence[float | None]) -> np.ndarray:
    """Average-rank normalization to [0, 1]; None ranks lowest (0.0).

    Ties share the average rank, so duplicated signal values contribute
    equally regardless of ordering.
    """
    n = len(values)
    if n == 0:
        return np.array([], dtype=np.float64)
    measured = {i: float(v) for i, v in enumerate(values) if v is not None}
    out = np.zeros(n, dtype=np.float64)
    present = list(measured)
    if not present:
        return out
    ordered = sorted(present, key=lambda i: measured[i])
    rank = 1
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and measured[ordered[j + 1]] == measured[ordered[i]]:
            j += 1
        avg = (rank + (rank + (j - i))) / 2.0
        for k in range(i, j + 1):
            out[ordered[k]] = avg
        rank += j - i + 1
        i = j + 1
    denominator = max(len(present) - 1, 1)
    out[present] = (out[present] - 1.0) / denominator
    return out


def _orientation(key: str, raw: Sequence[float | None]) -> list[float | None]:
    """Orient every signal so higher = more review-worthy before ranking."""
    if key in ("margin", "link_score"):
        return [None if v is None else 1.0 - float(v) for v in raw]
    return [None if v is None else float(v) for v in raw]


def priority_scores(
    signals: Sequence[ReviewSignal], weights: dict[str, float] | None = None
) -> np.ndarray:
    """Combined review priority per unit (higher = review sooner)."""
    w = weights or default_weights()
    total = float(sum(float(w.get(k, 0.0)) for k in WEIGHT_KEYS))
    priority = np.zeros(len(signals), dtype=np.float64)
    for key in WEIGHT_KEYS:
        raw = _orientation(key, [getattr(s, key) for s in signals])
        normalized = rank_normalize(raw)
        priority += float(w.get(key, 0.0)) * normalized
    if total > 0:
        priority /= total
    return priority


def top2_margins(code_scores: np.ndarray) -> np.ndarray:
    """Per-unit top1-top2 margin from an (n_units, n_codes) score matrix.

    A single code yields a maximal margin (no ambiguity expressible).
    """
    if code_scores.ndim != 2:
        raise ValueError("code_scores must be 2-D (n_units, n_codes)")
    n_codes = code_scores.shape[1]
    if n_codes < 2:
        return np.full(code_scores.shape[0], np.inf)
    ordered = np.sort(code_scores, axis=1)
    return ordered[:, -1] - ordered[:, -2]


def normalize_reason(raw: str) -> str:
    """Map a raw decision reason onto the CLOSED review-reason enum (r11).

    ``conformal`` / ``interpretive`` / ``overlap`` pass through; every other
    raw reason routes as the generic ``gray_zone`` uncertainty case.
    """
    if raw in REVIEW_REASONS:
        return raw
    return "gray_zone"


def route_reasons(
    margins: np.ndarray | Sequence[float], delta_amb: float, decision_reasons: Sequence[str]
) -> list[str]:
    """Closed-enum routing reason per unit.

    Top-2 margin below ``delta_amb`` (calibrated) routes with reason
    ``overlap``; otherwise the deductive decision's reason is normalized
    onto the enum.
    """
    if len(margins) != len(decision_reasons):
        raise ValueError("margins and decision_reasons must align")
    reasons: list[str] = []
    for margin, raw in zip(margins, decision_reasons, strict=True):
        if margin < delta_amb:
            reasons.append("overlap")
        else:
            reasons.append(normalize_reason(raw))
    return reasons


def fit_weights_l2(
    features: np.ndarray,
    labels: np.ndarray,
    l2: float = 1.0,
    lr: float = 0.1,
    iters: int = 2000,
) -> dict[str, float]:
    """L2-regularized logistic refit of w0..w6 against reviewer overrides.

    Args:
        features: (n_items, 7) rank-normalized signal columns, w0..w6 order.
        labels: (n_items,) 1 where the reviewer overrode the model.
        l2: Regularization strength (L2 penalty on weights, no bias term).
        lr: Full-batch gradient-descent learning rate.
        iters: Fixed iteration count (deterministic, no solver deps).

    Returns:
        {weight_key: fitted weight} clipped to be non-negative — priorities
        rank units, so signs are constrained to the uncertainty direction.

    Raises:
        InsufficientReviewDataError: below MIN_ITEMS_FOR_REFIT reviewed items
            (the old >=200 refit overfits and feeds back into the selector).
    """
    if features.ndim != 2 or features.shape[1] != len(WEIGHT_KEYS):
        raise ValueError(f"features must be (n, {len(WEIGHT_KEYS)})")
    n = features.shape[0]
    if n < MIN_ITEMS_FOR_REFIT:
        raise InsufficientReviewDataError(
            f"refit requires >= {MIN_ITEMS_FOR_REFIT} reviewed items, got {n}"
        )
    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    weights = np.full(len(WEIGHT_KEYS), 1.0 / len(WEIGHT_KEYS), dtype=np.float64)
    for _ in range(iters):
        logits = np.clip(x @ weights, -30.0, 30.0)
        preds = 1.0 / (1.0 + np.exp(-logits))
        grad = x.T @ (preds - y) / n + l2 * weights / n
        weights = np.clip(weights - lr * grad, 0.0, None)
    return {key: float(w) for key, w in zip(WEIGHT_KEYS, weights, strict=True)}
