"""Known-truth tests for confidence-signal evaluation + calibration (U47)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.coding.confidence_calibration import (
    auroc,
    brier_score,
    choose_routing_signal,
    evaluate_signal,
    expected_calibration_error,
    fit_logistic,
)


def test_auroc_perfect_reversed_and_ties() -> None:
    scores = np.array([0.9, 0.8, 0.3, 0.1])
    outcomes = np.array([1, 1, 0, 0])
    assert auroc(scores, outcomes) == pytest.approx(1.0)
    assert auroc(-scores, outcomes) == pytest.approx(0.0)
    # All-tied scores give coin-flip discrimination.
    assert auroc(np.ones(4), outcomes) == pytest.approx(0.5)


def test_auroc_undefined_for_single_class() -> None:
    assert auroc(np.array([0.2, 0.8]), np.array([1, 1])) is None


def test_ece_distinguishes_calibrated_from_overconfident() -> None:
    # Perfectly calibrated: p == accuracy within every bin.
    rng = np.random.default_rng(0)
    p = rng.random(20000)
    y = (rng.random(20000) < p).astype(float)
    assert expected_calibration_error(p, y) < 0.02
    # Overconfident: always says 1.0 but is right only half the time.
    overconfident = np.ones(100)
    half_right = np.concatenate([np.ones(50), np.zeros(50)])
    assert expected_calibration_error(overconfident, half_right) == pytest.approx(0.5)


def test_brier_known_values() -> None:
    assert brier_score(np.array([1.0, 0.0]), np.array([1, 0])) == 0.0
    assert brier_score(np.array([0.0, 1.0]), np.array([1, 0])) == 1.0
    assert brier_score(np.full(4, 0.5), np.array([1, 0, 1, 0])) == pytest.approx(0.25)


def test_platt_improves_calibration_of_discriminating_signal() -> None:
    rng = np.random.default_rng(1)
    n = 4000
    # Ground truth where Platt is correctly specified: correctness follows
    # sigmoid(2*raw - 1), so the raw signal treated as a probability is
    # badly miscalibrated while the logistic map is exactly recoverable.
    raw = rng.random(n)
    y = (rng.random(n) < 1.0 / (1.0 + np.exp(-(2.0 * raw - 1.0)))).astype(float)
    calibrator = fit_logistic(raw, y)
    calibrated = calibrator.predict(raw)
    ece_before = expected_calibration_error(np.clip(raw, 0, 1), y)
    ece_after = expected_calibration_error(calibrated, y)
    assert ece_after < ece_before / 2.0  # calibration actually repaired
    # Discrimination is preserved (monotone map).
    assert auroc(calibrated, y) == pytest.approx(auroc(raw, y), abs=1e-9)


def test_logistic_combination_learns_weights() -> None:
    rng = np.random.default_rng(2)
    n = 3000
    signal_good = rng.random(n)
    signal_noise = rng.random(n)
    p = 1 / (1 + np.exp(-(4 * (signal_good - 0.5))))
    y = (rng.random(n) < p).astype(float)
    x = np.column_stack([signal_good, signal_noise])
    calibrator = fit_logistic(x, y)
    assert calibrator.kind == "logistic_combination"
    assert abs(calibrator.weights[0]) > abs(calibrator.weights[1]) * 3


def test_evaluate_signal_bundle() -> None:
    scores = np.array([0.9, 0.8, 0.2, 0.1])
    outcomes = np.array([1, 0, 0, 0])
    ev = evaluate_signal("test", scores, outcomes)
    assert ev.signal == "test"
    # The single positive (0.9) outranks all three negatives -> AUROC 1.
    assert ev.auroc == pytest.approx(1.0)
    assert ev.n == 4
    assert 0.0 <= ev.ece <= 1.0
    assert 0.0 <= ev.brier <= 1.0


def test_choose_routing_signal_picks_informative_signal() -> None:
    rng = np.random.default_rng(3)
    n = 1500
    p = rng.random(n)
    y = (rng.random(n) < p).astype(float)
    informative = np.where(y == 1, rng.uniform(0.6, 1.0, n), rng.uniform(0.0, 0.4, n))
    noise = rng.random(n)
    split = n // 2
    choice = choose_routing_signal(
        {"informative": informative[:split], "noise": noise[:split]},
        y[:split],
        {"informative": informative[split:], "noise": noise[split:]},
        y[split:],
    )
    assert choice.status == "ok"
    assert choice.chosen_signal == "informative"
    assert choice.calibrator.kind == "platt"
    assert len(choice.test_metrics) == 1  # evaluated ONCE on test
    assert choice.test_metrics[0].auroc is not None
    assert choice.test_metrics[0].auroc > 0.9
    assert "once" in choice.reason


def test_choose_routing_signal_insufficient_data() -> None:
    choice = choose_routing_signal(
        {"s": np.array([0.1, 0.9, 0.5])},
        np.array([1, 1, 1]),  # single class: nothing is evaluable
        {"s": np.array([0.1])},
        np.array([0]),
    )
    assert choice.status == "insufficient_data"
    assert choice.chosen_signal == ""
    assert choice.calibrator.kind == "identity"


def test_choose_routing_is_deterministic() -> None:
    rng = np.random.default_rng(7)
    n = 800
    p = rng.random(n)
    y = (rng.random(n) < p).astype(float)
    s = np.clip(p + rng.normal(0, 0.1, n), 0, 1)
    split = n // 2
    a = choose_routing_signal({"s": s[:split]}, y[:split], {"s": s[split:]}, y[split:])
    b = choose_routing_signal({"s": s[:split]}, y[:split], {"s": s[split:]}, y[split:])
    assert a == b


def test_isotonic_only_when_sklearn_available() -> None:
    """prefer_isotonic stays sklearn-free when the extra is absent."""
    rng = np.random.default_rng(11)
    n = 1000
    p = rng.random(n)
    y = (rng.random(n) < p).astype(float)
    s = np.clip(p + rng.normal(0, 0.05, n), 0, 1)
    split = n // 2
    choice = choose_routing_signal(
        {"s": s[:split]}, y[:split], {"s": s[split:]}, y[split:], prefer_isotonic=True
    )
    assert choice.status == "ok"
    # With sklearn missing the local logistic path is used; with it present
    # the isotonic step function must map [0,1] into [0,1] either way.
    preds = choice.calibrator.predict(s[split:])
    assert np.all((preds >= 0.0) & (preds <= 1.0))
