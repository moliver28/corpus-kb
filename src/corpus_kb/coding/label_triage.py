"""Label-error triage without Cleanlab (U39; v8 §3 U39).

K-fold cross-validated OUT-OF-SAMPLE predicted probabilities over the
existing labels; units whose ASSIGNED label has low out-of-sample
probability (the confident-learning idea, implemented from scratch —
Cleanlab itself is AGPL-3.0 and rejected) are ranked into a
``label_review_queue`` for humans. NEVER auto-relabels: the only output is
a ranked queue plus, optionally, the precision of the flags measured on a
human-checked sample.

The fold predictor is INJECTED (a callable fitting on train features/labels
and returning predicted class probabilities for eval rows), so no model
dependency enters this module. A deterministic numpy multinomial-logistic
factory is provided for callers without their own scorer.

Pure numpy; deterministic under a fixed seed (stratified fold assignment).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

INSUFFICIENT_DATA = "insufficient_data"
OK = "ok"


@dataclass(frozen=True)
class LabelReviewItem:
    """One ranked entry of the label_review_queue (humans decide, not us)."""

    unit_index: int
    assigned_label: int
    oos_probability: float  # out-of-sample P(assigned label)
    runner_up_label: int
    runner_up_probability: float


@dataclass(frozen=True)
class LabelTriageResult:
    """Ranked label-review queue for one labeling pass (U39)."""

    status: str  # ok | insufficient_data
    queue: tuple[LabelReviewItem, ...]  # ascending out-of-sample probability
    n_units: int
    n_flagged: int
    cv_folds: int
    flag_threshold: float
    reason: str


FoldPredictor = Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]
"""(X_train, y_train, X_eval) -> (n_eval, n_classes) predicted probabilities."""


def _stratified_folds(labels: np.ndarray, cv_folds: int, seed: int) -> np.ndarray:
    """Fold index per unit; stratified so every fold sees every class."""
    rng = np.random.default_rng(seed)
    fold_of = np.zeros(labels.size, dtype=int)
    for cls in np.unique(labels):
        idx = np.where(labels == cls)[0]
        rng.shuffle(idx)
        for pos, unit in enumerate(idx):
            fold_of[unit] = pos % cv_folds
    return fold_of


def label_triage(
    features: np.ndarray,
    labels: np.ndarray,
    *,
    fold_predictor: FoldPredictor,
    cv_folds: int = 5,
    flag_threshold: float = 0.2,
    seed: int = 0,
) -> LabelTriageResult:
    """Rank units whose assigned label has low out-of-sample support.

    Args:
        features: (n_units, n_features) embedding/prototype features.
        labels: (n_units,) assigned labels (int class indices).
        fold_predictor: Injected scorer; fitted per fold on the training
            split, applied to the held-out split (never sees its own rows).
        cv_folds: K in K-fold CV (default 5; each fold needs >= 1 row).
        flag_threshold: Assigned-label out-of-sample probability below
            which a unit is flagged for review.
        seed: Fold-assignment seed (deterministic output for fixed inputs).

    Returns:
        LabelTriageResult with the queue ranked WORST label support first.
        Fewer than ``2 * cv_folds`` units or fewer than 2 classes ->
        ``insufficient_data`` with an empty queue.
    """
    x = np.asarray(features, dtype=float)
    y = np.asarray(labels, dtype=int).ravel()
    if x.shape[0] != y.size or x.shape[0] == 0:
        raise ValueError("features and labels must align and be non-empty")
    n_classes = int(y.max()) + 1
    if y.size < 2 * cv_folds or n_classes < 2:
        return LabelTriageResult(
            status=INSUFFICIENT_DATA,
            queue=(),
            n_units=int(y.size),
            n_flagged=0,
            cv_folds=cv_folds,
            flag_threshold=flag_threshold,
            reason=(
                f"triage needs >= {2 * cv_folds} units and >= 2 classes; "
                f"got {y.size} units, {n_classes} classes"
            ),
        )

    fold_of = _stratified_folds(y, cv_folds, seed)
    oos = np.full((y.size, n_classes), np.nan)
    for fold in range(cv_folds):
        train = fold_of != fold
        eval_rows = fold_of == fold
        if not np.any(train) or not np.any(eval_rows):
            continue
        probs = np.asarray(fold_predictor(x[train], y[train], x[eval_rows]), dtype=float)
        if probs.shape != (int(np.sum(eval_rows)), n_classes):
            raise ValueError(
                f"fold_predictor returned {probs.shape}, expected "
                f"({int(np.sum(eval_rows))}, {n_classes})"
            )
        oos[eval_rows] = probs
    assigned_prob = oos[np.arange(y.size), y]
    order = np.argsort(assigned_prob, kind="stable")  # worst support first
    queue: list[LabelReviewItem] = []
    for i in order:
        if assigned_prob[i] >= flag_threshold:
            break
        probs_row = oos[i].copy()
        probs_row[y[i]] = -np.inf
        runner_up = int(np.argmax(probs_row))
        queue.append(
            LabelReviewItem(
                unit_index=int(i),
                assigned_label=int(y[i]),
                oos_probability=float(assigned_prob[i]),
                runner_up_label=runner_up,
                runner_up_probability=float(probs_row[runner_up]),
            )
        )
    return LabelTriageResult(
        status=OK,
        queue=tuple(queue),
        n_units=int(y.size),
        n_flagged=len(queue),
        cv_folds=cv_folds,
        flag_threshold=flag_threshold,
        reason=(
            f"{len(queue)}/{y.size} units below out-of-sample support "
            f"{flag_threshold}; queue is advisory - never auto-relabel"
        ),
    )


def precision_of_flags(
    flagged_unit_indices: Sequence[int],
    human_checked_indices: Sequence[int],
    confirmed_mislabels: Sequence[int],
) -> float | None:
    """Precision of the triage queue on a human-checked sample (U39 metric).

    Args:
        flagged_unit_indices: Unit indices the queue flagged.
        human_checked_indices: Unit indices a human actually reviewed.
        confirmed_mislabels: Among the checked ones, the confirmed label errors.

    Returns:
        (#flags confirmed as mislabels) / (#flags checked), or None when no
        flagged unit was checked (honest unknown, not 0).
    """
    flagged = {int(i) for i in flagged_unit_indices}
    checked = {int(i) for i in human_checked_indices}
    mislabels = {int(i) for i in confirmed_mislabels}
    hits = flagged & checked & mislabels
    checked_flags = flagged & checked
    if not checked_flags:
        return None
    return len(hits) / len(checked_flags)


def logistic_fold_predictor(*, l2: float = 1.0, lr: float = 0.1, iters: int = 500) -> FoldPredictor:
    """Deterministic numpy multinomial-logistic fold predictor (convenience).

    Full-batch gradient descent, zero init, fixed iterations; softmax over
    class columns. For callers that do not bring their own deductive scorer.
    """

    def _predict(x_train: np.ndarray, y_train: np.ndarray, x_eval: np.ndarray) -> np.ndarray:
        n_classes = int(y_train.max()) + 1
        y_onehot = np.zeros((y_train.size, n_classes), dtype=float)
        y_onehot[np.arange(y_train.size), y_train] = 1.0
        w = np.zeros((x_train.shape[1], n_classes), dtype=float)
        b = np.zeros(n_classes, dtype=float)
        n = x_train.shape[0]
        for _ in range(iters):
            logits = x_train @ w + b
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(axis=1, keepdims=True)
            grad_w = x_train.T @ (p - y_onehot) / n + l2 * w / n
            grad_b = (p - y_onehot).mean(axis=0)
            w -= lr * grad_w
            b -= lr * grad_b
        eval_logits = x_eval @ w + b
        eval_logits -= eval_logits.max(axis=1, keepdims=True)
        out = np.exp(eval_logits)
        out /= out.sum(axis=1, keepdims=True)
        return out

    return _predict
