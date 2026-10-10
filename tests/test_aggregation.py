"""Known-truth tests for Dawid-Skene EM aggregation + weak supervision (U33/U38)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.coding.aggregation import (
    MISSING,
    aggregate_signals,
    dawid_skene,
    evaluate_agreement,
)


def _rng_annotations(truth: np.ndarray, accuracies: list[float], seed: int) -> np.ndarray:
    """(n_items, n_annotators) labels drawn at per-annotator accuracy."""
    rng = np.random.default_rng(seed)
    out = np.empty((truth.size, len(accuracies)), dtype=int)
    for j, acc in enumerate(accuracies):
        flip = rng.random(truth.size) > acc
        out[:, j] = np.where(flip, 1 - truth, truth)
    return out


def test_perfect_annotators_recover_truth() -> None:
    truth = np.array([0, 1, 1, 0, 2, 2, 1, 0])
    annotations = np.tile(truth[:, None], (1, 3))
    result = dawid_skene(annotations, n_classes=3)
    assert np.array_equal(result.labels, truth)
    # Posteriors put (numerically) all mass on the planted truth.
    assert np.allclose(result.posterior.max(axis=1), 1.0, atol=1e-6)
    assert result.converged
    # Perfect annotators have identity confusion matrices.
    for j in range(3):
        assert np.allclose(result.error_rates[j], np.eye(3), atol=1e-6)


def test_adversarial_annotator_down_weighted() -> None:
    truth = np.array([0, 1, 0, 1, 0, 1, 0, 1, 0, 1] * 5)
    # Two good annotators (90%), one adversarial (answers mostly wrong).
    annotations = _rng_annotations(truth, [0.9, 0.9, 0.15], seed=7)
    result = dawid_skene(annotations, n_classes=2, max_iter=200)
    good_accuracy = [float(np.trace(result.error_rates[j])) / 2 for j in range(2)]
    adversarial_accuracy = float(np.trace(result.error_rates[2])) / 2
    assert min(good_accuracy) > 0.8
    assert adversarial_accuracy < 0.5  # learned to distrust the adversarial rater
    # Reference labels still track the planted truth.
    assert float(np.mean(result.labels == truth)) >= 0.95


def test_posteriors_sum_to_one_and_are_finite() -> None:
    truth = np.array([0, 1, 1, 0, 1])
    annotations = _rng_annotations(truth, [0.8, 0.6, 0.7], seed=3)
    result = dawid_skene(annotations, n_classes=2)
    assert np.allclose(result.posterior.sum(axis=1), 1.0)
    assert np.isfinite(result.posterior).all()
    # Rows of every confusion matrix sum to 1.
    for j in range(annotations.shape[1]):
        assert np.allclose(result.error_rates[j].sum(axis=1), 1.0)


def test_missing_ratings_do_not_break_em() -> None:
    truth = np.array([0, 1, 1, 0, 1, 0, 1, 0])
    annotations = _rng_annotations(truth, [0.9, 0.85, 0.9], seed=11)
    annotations[0, 1] = MISSING
    annotations[3, 0] = MISSING
    annotations[5, 2] = MISSING
    result = dawid_skene(annotations, n_classes=2)
    assert result.n_observed == annotations.size - 3
    assert float(np.mean(result.labels == truth)) >= 0.75


def test_dawid_skene_is_deterministic() -> None:
    truth = np.array([0, 1, 1, 0, 1, 0, 1, 0, 1, 1])
    annotations = _rng_annotations(truth, [0.8, 0.75, 0.7], seed=5)
    a = dawid_skene(annotations, n_classes=2, max_iter=50)
    b = dawid_skene(annotations, n_classes=2, max_iter=50)
    assert np.array_equal(a.labels, b.labels)
    assert np.array_equal(a.posterior, b.posterior)
    assert np.array_equal(a.error_rates, b.error_rates)


def test_invalid_inputs_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        dawid_skene(np.empty((0, 2), dtype=int), n_classes=2)
    with pytest.raises(ValueError, match="n_classes"):
        dawid_skene(np.array([[0, 3]]), n_classes=2)


def test_weak_supervision_em_recovers_signal_accuracy() -> None:
    truth = np.array([0, 1, 0, 1, 0, 1, 0, 1, 0, 1] * 4)
    signals = _rng_annotations(truth, [0.9, 0.85, 0.8, 0.7], seed=13)
    result = aggregate_signals(signals, n_classes=2, method="em")
    assert result.status == "ok"
    assert result.signal_accuracy is not None
    # Learned accuracies rank like the planted ones.
    assert result.signal_accuracy[0] > result.signal_accuracy[3]
    # With a planted 0.7-accuracy signal in the panel, the EM-adjudicated
    # labels track truth well above the weakest single signal.
    assert float(np.mean(result.labels == truth)) >= 0.8


def test_weak_supervision_insufficient_signals_falls_back_to_vote() -> None:
    signals = np.array([[0, 1], [1, 1], [0, 0], [1, 0]])
    result = aggregate_signals(signals, n_classes=2, min_signals=3, method="em")
    assert result.status == "insufficient_signals"
    assert result.signal_accuracy is None
    # Majority vote on 2 disagreeing signals is a tie -> review.
    assert bool(result.review[0]) and bool(result.review[3])
    # Agreeing pairs label cleanly.
    assert not result.review[1] and not result.review[2]


def test_weak_supervision_vote_tie_routes_to_review() -> None:
    signals = np.array([[0, 1, 2]])
    result = aggregate_signals(signals, n_classes=3, min_signals=3, method="vote")
    assert result.status == "ok"  # enough signals, vote method chosen
    assert bool(result.review[0])  # three-way tie


def test_weak_supervision_abstain_on_low_confidence() -> None:
    # Anchor items pin every signal to a near-identity confusion; the final
    # item has NO signal observation at all, so its posterior is exactly the
    # class prior (1/3 each) and must abstain rather than fabricate a label.
    anchors = [[0, 0, 0], [1, 1, 1], [2, 2, 2]] * 3
    signals = np.array([*anchors, [MISSING, MISSING, MISSING]], dtype=int)
    result = aggregate_signals(signals, n_classes=3, method="em", abstain_threshold=0.6)
    assert result.status == "ok"
    assert result.abstain[-1]  # the unobserved item abstains
    assert not result.abstain[:-1].any()  # anchored items stay confident
    assert np.allclose(result.probabilities[-1], 1.0 / 3.0)
    # Review is a superset of abstain (abstained items always go to review).
    assert np.array_equal(result.review, result.review | result.abstain)


def test_evaluate_agreement_heldout() -> None:
    predicted = np.array([0, 1, 1, 0])
    truth = np.array([0, 1, 0, 0])
    stats = evaluate_agreement(predicted, truth)
    assert stats == {"n": 4, "accuracy": 0.75}
    empty = evaluate_agreement(predicted, truth, mask=np.array([False] * 4))
    assert empty == {"n": 0, "accuracy": 0.0}
