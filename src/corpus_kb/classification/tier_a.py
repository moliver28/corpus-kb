"""Tier A: the logistic head on the stored vectors (WP-3, default tier).

sklearn ``LogisticRegression`` trained on the seed rows only — no new
inference pass, minutes on CPU. The shared head math lives here: tier B's
frozen-body setfit model is the same logistic head over body embeddings,
so both tiers score corpora through :func:`predict_from_scores`.

DETERMINISM (F14): every fit and every score pass runs inside
``threadpoolctl.threadpool_limits(1)``. BLAS thread counts change
floating-point summation order; pinned to one thread, the same data on
the same machine gives bitwise-equal predictions. The lbfgs solver is
deterministic by construction. Every model parameter is recorded in
``params`` so a harness can snapshot a build.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

__all__ = ["HeadFit", "fit_head", "head_estimator", "predict_from_scores"]

HEAD_SOLVER = "lbfgs"
HEAD_C = 1.0
HEAD_MAX_ITER = 1000
SAMPLE_POLICY = "seed_rows_only"


def head_estimator() -> LogisticRegression:
    """The one head spec (fit, OOF folds, and Platt all reuse it)."""
    return LogisticRegression(solver=HEAD_SOLVER, C=HEAD_C, max_iter=HEAD_MAX_ITER)


@dataclass(frozen=True)
class HeadFit:
    """A fitted head: the model, the class order, and the params record."""

    model: LogisticRegression
    classes: list[str]
    params: dict[str, object]

    def decision_scores(self, features: np.ndarray) -> np.ndarray:
        """Raw decision scores over feature rows (single-thread pinned)."""
        with threadpool_limits(1):
            return np.asarray(self.model.decision_function(features))


def fit_head(features: np.ndarray, labels: np.ndarray) -> HeadFit:
    """Fit the head on the seed rows; record the determinism contract.

    Build-level keys (seed file hash, corpus counts) are merged by the
    orchestrator; the fit records its own tier, solver, and versions.
    """
    classes = sorted({str(label) for label in labels})
    model = head_estimator()
    with threadpool_limits(1):
        model.fit(features, [str(label) for label in labels])
    params: dict[str, object] = {
        "tier": "head",
        "solver": HEAD_SOLVER,
        "C": HEAD_C,
        "max_iter": HEAD_MAX_ITER,
        "sklearn_version": sklearn.__version__,
        "n_train": len(labels),
        "classes": classes,
        "sample_policy": SAMPLE_POLICY,
    }
    return HeadFit(model=model, classes=classes, params=params)


def predict_from_scores(scores: np.ndarray, classes: list[str]) -> tuple[list[str], np.ndarray]:
    """Shared head math: decision scores -> labels + raw probabilities.

    Binary scores arrive as one column (sigmoid); multiclass scores as one
    column per class (softmax). Ties break to the lowest class index, so
    the label order is stable for the snapshot harness.
    """
    if scores.ndim == 1:
        positive = _sigmoid(scores)
        probabilities = np.stack([1.0 - positive, positive], axis=1)
    else:
        shifted = scores - scores.max(axis=1, keepdims=True)
        weights = np.exp(shifted)
        probabilities = weights / weights.sum(axis=1, keepdims=True)
    winners = probabilities.argmax(axis=1)
    labels = [classes[int(index)] for index in winners]
    return labels, probabilities


def _sigmoid(values: np.ndarray) -> np.ndarray:
    """The overflow-stable sigmoid."""
    out = np.empty_like(values, dtype=np.float64)
    positive = values >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exp_values = np.exp(values[~positive])
    out[~positive] = exp_values / (1.0 + exp_values)
    return out
