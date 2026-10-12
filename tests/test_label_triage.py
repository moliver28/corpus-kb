"""Known-truth tests for label-error triage (U39; no Cleanlab, own implementation)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.coding.label_triage import (
    INSUFFICIENT_DATA,
    OK,
    label_triage,
    logistic_fold_predictor,
    precision_of_flags,
)


def _clean_dataset(n_per_class: int = 60, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Two well-separated gaussian blobs with clean labels."""
    rng = np.random.default_rng(seed)
    class0 = rng.normal(0.0, 0.5, size=(n_per_class, 4))
    class1 = rng.normal(4.0, 0.5, size=(n_per_class, 4))
    x = np.vstack([class0, class1])
    y = np.array([0] * n_per_class + [1] * n_per_class)
    return x, y


def test_clean_labels_produce_few_flags() -> None:
    x, y = _clean_dataset()
    result = label_triage(
        x, y, fold_predictor=logistic_fold_predictor(), cv_folds=5, flag_threshold=0.2, seed=0
    )
    assert result.status == OK
    assert result.n_flagged <= 3  # clean separable data: almost nothing flagged
    assert result.n_units == 2 * 60


def test_planted_mislabels_rank_top_of_queue() -> None:
    x, y = _clean_dataset(n_per_class=80, seed=1)
    # Plant 8 obvious label errors: class-1 features labeled 0 and vice versa.
    mislabel_idx = list(range(0, 8)) + list(range(80, 88))
    y_planted = y.copy()
    y_planted[mislabel_idx] = 1 - y_planted[mislabel_idx]
    result = label_triage(
        x,
        y_planted,
        fold_predictor=logistic_fold_predictor(),
        cv_folds=5,
        flag_threshold=0.3,
        seed=0,
    )
    assert result.status == OK
    assert result.n_flagged >= 8
    top = [item.unit_index for item in result.queue[:12]]
    hit = len(set(top) & set(mislabel_idx))
    assert hit >= 8  # planted errors dominate the head of the queue
    # Queue is ranked worst out-of-sample support first.
    probs = [item.oos_probability for item in result.queue]
    assert probs == sorted(probs)
    # No auto-relabel: every item keeps its ASSIGNED label for humans to see.
    for item in result.queue:
        assert item.assigned_label == y_planted[item.unit_index]


def test_insufficient_data_too_few_units_or_classes() -> None:
    x, y = _clean_dataset(n_per_class=4)
    result = label_triage(x, y, fold_predictor=logistic_fold_predictor(), cv_folds=5, seed=0)
    assert result.status == INSUFFICIENT_DATA
    assert result.queue == ()
    x2, _ = _clean_dataset()
    single_class = np.zeros(120, dtype=int)
    result2 = label_triage(x2, single_class, fold_predictor=logistic_fold_predictor(), seed=0)
    assert result2.status == INSUFFICIENT_DATA


def test_wrong_predictor_shape_rejected() -> None:
    x, y = _clean_dataset(n_per_class=12)

    def bad_predictor(x_train: np.ndarray, y_train: np.ndarray, x_eval: np.ndarray) -> np.ndarray:
        return np.ones((len(x_eval), 1))  # wrong class count

    with pytest.raises(ValueError, match="fold_predictor"):
        label_triage(x, y, fold_predictor=bad_predictor, cv_folds=3, seed=0)


def test_fold_predictor_never_sees_its_own_rows() -> None:
    x, y = _clean_dataset(n_per_class=12)
    seen: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []

    def spy(x_train: np.ndarray, y_train: np.ndarray, x_eval: np.ndarray) -> np.ndarray:
        seen.append((x_train, y_train, x_eval))
        return logistic_fold_predictor()(x_train, y_train, x_eval)

    label_triage(x, y, fold_predictor=spy, cv_folds=4, seed=0)
    assert len(seen) == 4
    for x_train, _, x_eval in seen:
        # No eval row may appear in the fold's training rows.
        for row in x_eval:
            assert not np.any(np.all(np.isclose(x_train, row), axis=1))


def test_triage_is_deterministic_under_seed() -> None:
    x, y = _clean_dataset(n_per_class=30, seed=5)
    y_noisy = y.copy()
    y_noisy[:4] = 1 - y_noisy[:4]
    a = label_triage(x, y_noisy, fold_predictor=logistic_fold_predictor(), cv_folds=4, seed=3)
    b = label_triage(x, y_noisy, fold_predictor=logistic_fold_predictor(), cv_folds=4, seed=3)
    assert a == b


def test_precision_of_flags() -> None:
    flagged = [1, 2, 3, 4]
    checked = [2, 3, 8]
    mislabels = [3, 8]
    # Only units 2 and 3 were both flagged and checked; 3 is a true mislabel.
    assert precision_of_flags(flagged, checked, mislabels) == pytest.approx(0.5)
    # No flagged unit checked -> honest None, not 0.
    assert precision_of_flags(flagged, [9, 10], [9]) is None
