"""Tier A (logistic head) and calibration: determinism and honesty (WP-3).

The fit runs on synthetic separable vectors, so the assertions check the
math, not a model download. F14: two fits on the same data must agree
bitwise (the single-thread pin holds inside one process). F2: calibration
on out-of-fold scores must sharpen the probabilities, and a seed set too
thin for folds must skip honestly.
"""

from __future__ import annotations

import numpy as np
import pytest
import sklearn

from corpus_kb.classification import calibrate, tier_a


def _blobs(n_per_class: int = 16, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    alpha = rng.normal(loc=[4.0, 0.0], scale=0.3, size=(n_per_class, 2))
    beta = rng.normal(loc=[0.0, 4.0], scale=0.3, size=(n_per_class, 2))
    features = np.vstack([alpha, beta]).astype(np.float32)
    labels = np.asarray(["alpha"] * n_per_class + ["beta"] * n_per_class)
    return features, labels


def test_fit_head_predicts_separable_blobs() -> None:
    features, labels = _blobs()
    fitted = tier_a.fit_head(features, labels)
    predicted, probabilities = tier_a.predict_from_scores(
        fitted.decision_scores(features), fitted.classes
    )
    assert predicted == list(labels)
    assert probabilities.shape == (len(labels), 2)
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert float(probabilities.max(axis=1).mean()) > 0.9


def test_fit_head_records_the_determinism_params() -> None:
    features, labels = _blobs()
    params = tier_a.fit_head(features, labels).params
    assert params["tier"] == "head"
    assert params["solver"] == "lbfgs"
    assert params["C"] == tier_a.HEAD_C
    assert params["max_iter"] == tier_a.HEAD_MAX_ITER
    assert params["sklearn_version"] == sklearn.__version__
    assert params["n_train"] == len(labels)
    assert params["classes"] == ["alpha", "beta"]
    assert params["sample_policy"] == "seed_rows_only"


def test_two_fits_agree_bitwise() -> None:
    features, labels = _blobs()
    first = tier_a.fit_head(features, labels)
    second = tier_a.fit_head(features, labels)
    scores_one = first.decision_scores(features)
    scores_two = second.decision_scores(features)
    assert np.array_equal(scores_one, scores_two)
    labels_one, proba_one = tier_a.predict_from_scores(scores_one, first.classes)
    labels_two, proba_two = tier_a.predict_from_scores(scores_two, second.classes)
    assert labels_one == labels_two
    assert np.array_equal(proba_one, proba_two)


def test_multiclass_scores_softmax_to_full_probabilities() -> None:
    rng = np.random.default_rng(1)
    classes = [(f"c{i}", 6.0 * np.eye(3)[i]) for i in range(3)]
    features = np.vstack(
        [rng.normal(loc=center, scale=0.2, size=(12, 3)) for _name, center in classes]
    ).astype(np.float32)
    labels = np.asarray([name for name, _center in classes for _ in range(12)])
    fitted = tier_a.fit_head(features, labels)
    predicted, probabilities = tier_a.predict_from_scores(
        fitted.decision_scores(features), fitted.classes
    )
    assert fitted.classes == ["c0", "c1", "c2"]
    assert predicted == list(labels)
    assert probabilities.shape == (36, 3)
    assert np.allclose(probabilities.sum(axis=1), 1.0)


def test_calibration_sharpens_separable_probabilities() -> None:
    features, labels = _blobs()
    fitted = tier_a.fit_head(features, labels)
    scores = fitted.decision_scores(features)
    _labels, raw = tier_a.predict_from_scores(scores, fitted.classes)
    oof, cv, status = calibrate.out_of_fold_scores(features, labels, fitted.classes)
    assert status == "platt"
    assert cv == 5
    assert oof is not None
    calibrated = calibrate.apply_platt(calibrate.fit_platt(oof, labels, fitted.classes, cv), scores)
    assert calibrated.shape == raw.shape
    assert np.allclose(calibrated.sum(axis=1), 1.0)
    assert float(calibrated.max(axis=1).mean()) > float(raw.max(axis=1).mean())


def test_calibration_skips_when_a_class_holds_one_seed() -> None:
    features, _labels = _blobs()
    thin = np.vstack([features[:1], features[16:]])
    thin_labels = np.asarray(["alpha", "beta"])
    oof, cv, status = calibrate.out_of_fold_scores(thin, thin_labels, ["alpha", "beta"])
    assert oof is None
    assert cv is None
    assert status == calibrate.SKIPPED_TOO_FEW


def test_multiclass_calibration_keeps_row_stochastic_output() -> None:
    rng = np.random.default_rng(2)
    centers = [8.0 * np.eye(3)[i] for i in range(3)]
    features = np.vstack(
        [rng.normal(loc=center, scale=0.25, size=(10, 3)) for center in centers]
    ).astype(np.float32)
    labels = np.asarray(["c0"] * 10 + ["c1"] * 10 + ["c2"] * 10)
    classes = ["c0", "c1", "c2"]
    fitted = tier_a.fit_head(features, labels)
    scores = fitted.decision_scores(features)
    oof, cv, status = calibrate.out_of_fold_scores(features, labels, classes)
    assert status == "platt" and oof is not None
    calibrated = calibrate.apply_platt(calibrate.fit_platt(oof, labels, classes, cv), scores)
    assert calibrated.shape == (30, 3)
    assert np.allclose(calibrated.sum(axis=1), 1.0)
    assert float(calibrated.max(axis=1).mean()) > 0.9


def test_binary_oof_scores_are_one_column() -> None:
    features, labels = _blobs(n_per_class=3)
    oof, cv, status = calibrate.out_of_fold_scores(features, labels, ["alpha", "beta"])
    assert status == "platt" and oof is not None
    assert oof.ndim == 1
    assert cv == 3


@pytest.mark.parametrize(
    ("scores", "classes", "expected_first"),
    [
        (np.array([-2.0, 0.5, 3.0]), ["a", "b"], "a"),
        (np.array([[0.1, 0.9], [2.0, -1.0]]), ["a", "b"], "b"),
    ],
    ids=["binary-margin", "two-column"],
)
def test_predict_from_scores_tiebreaks_stably(
    scores: np.ndarray, classes: list[str], expected_first: str
) -> None:
    labels, probabilities = tier_a.predict_from_scores(scores, classes)
    assert labels[0] == expected_first
    assert np.all(probabilities >= 0.0) and np.all(probabilities <= 1.0)
