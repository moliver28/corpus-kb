"""Post-hoc probability calibration for the classification family (WP-3).

Upstream ``predict_proba`` on few-shot heads is documented unreliable
(setfit #479/#462/#310), and the review routing reads the confidence, so
every build calibrates before it flags. Method: Platt scaling — one
logistic calibrator per class, fit on OUT-OF-FOLD decision scores of the
same head spec (``cross_val_predict``), applied to the final model's
scores. Honest skip: with fewer than two seeds in any class there is no
fold to hold out, so the build records
``calibration="skipped:too_few_seeds"`` and scores with raw probabilities.
Never crashes on small seed sets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict
from threadpoolctl import threadpool_limits

from .tier_a import head_estimator

__all__ = ["MAX_CV_FOLDS", "Calibration", "apply_platt", "fit_platt", "out_of_fold_scores"]

MAX_CV_FOLDS = 5
SKIPPED_TOO_FEW = "skipped:too_few_seeds"

_PLATT_SOLVER = "lbfgs"
_PLATT_MAX_ITER = 1000


@dataclass(frozen=True)
class Calibration:
    """The fitted per-class Platt calibrator (or the honest skip)."""

    status: str  # "platt" or SKIPPED_TOO_FEW
    cv: int | None
    models: tuple[tuple[str, LogisticRegression], ...] = ()


def out_of_fold_scores(
    features: np.ndarray, labels: np.ndarray, classes: list[str]
) -> tuple[np.ndarray | None, int | None, str]:
    """Out-of-fold decision scores of the shared head spec.

    Returns ``(scores, cv, status)``; scores are ``None`` and the status
    ``skipped:too_few_seeds`` when any class holds fewer than two seeds.
    """
    counts = {cls: int(np.sum(labels == cls)) for cls in classes}
    min_count = min(counts.values())
    if min_count < 2:
        return None, None, SKIPPED_TOO_FEW
    cv = min(MAX_CV_FOLDS, min_count)
    with threadpool_limits(1):
        scores = cross_val_predict(
            head_estimator(),
            features,
            [str(label) for label in labels],
            cv=cv,
            method="decision_function",
        )
    return np.asarray(scores), cv, "platt"


def fit_platt(
    scores: np.ndarray, labels: np.ndarray, classes: list[str], cv: int | None
) -> Calibration:
    """Fit one Platt calibrator per class on the out-of-fold scores.

    Binary scores arrive as one column; both classes calibrate the same
    margin. Multiclass scores arrive as one column per class, so each
    class calibrates its own column against its own one-vs-rest target.
    """
    matrix = scores if scores.ndim == 2 else scores.reshape(-1, 1)
    fitted: list[tuple[str, LogisticRegression]] = []
    with threadpool_limits(1):
        for index, cls in enumerate(classes):
            column = matrix[:, index] if matrix.shape[1] > 1 else matrix[:, 0]
            target = (labels == cls).astype(int)
            calibrator = LogisticRegression(solver=_PLATT_SOLVER, max_iter=_PLATT_MAX_ITER)
            calibrator.fit(column.reshape(-1, 1), target)
            fitted.append((cls, calibrator))
    return Calibration(status="platt", cv=cv, models=tuple(fitted))


def apply_platt(calibration: Calibration, raw_scores: np.ndarray) -> np.ndarray:
    """Calibrated probabilities over score rows (rows sum to one)."""
    matrix = raw_scores if raw_scores.ndim == 2 else raw_scores.reshape(-1, 1)
    columns: list[np.ndarray] = []
    with threadpool_limits(1):
        for index, (_cls, calibrator) in enumerate(calibration.models):
            column = matrix[:, index] if matrix.shape[1] > 1 else matrix[:, 0]
            columns.append(calibrator.predict_proba(column.reshape(-1, 1))[:, 1])
    stacked = np.vstack(columns).T
    totals = stacked.sum(axis=1, keepdims=True)
    totals[totals == 0.0] = 1.0
    return stacked / totals
