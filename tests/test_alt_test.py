"""Known-truth tests for the Alternative Annotator Test (U13; Calderon et al. 2025)."""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from corpus_kb.coding.alt_test import (
    FAIL,
    INSUFFICIENT_DATA,
    PASS,
    alt_test,
)


def _panel_data(
    n_units: int,
    annotator_accuracy: list[float],
    model_accuracy: float,
    seed: int,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Synthetic panel: planted truth, noisy humans, one model candidate."""
    rng = np.random.default_rng(seed)
    truth = rng.integers(0, 2, n_units)

    def noisy(accuracy: float) -> np.ndarray:
        return np.where(rng.random(n_units) < accuracy, truth, 1 - truth)

    annotators = {f"h{j}": noisy(acc) for j, acc in enumerate(annotator_accuracy)}
    annotations: dict[str, dict[str, str]] = {}
    for i in range(n_units):
        annotations[f"u{i}"] = {name: str(labels[i]) for name, labels in annotators.items()}
    model_labels = noisy(model_accuracy)
    model_preds = {f"u{i}": str(model_labels[i]) for i in range(n_units)}
    return annotations, model_preds


def test_insufficient_data_below_three_annotators() -> None:
    annotations = {
        "u1": {"a": "1", "b": "0"},
        "u2": {"a": "0", "b": "0"},
    }
    result = alt_test(annotations, {"u1": "1", "u2": "1"})
    assert result.status == INSUFFICIENT_DATA
    assert result.winning_rate is None
    assert result.verdicts == ()


def test_insufficient_data_single_rater_units_only() -> None:
    # Three annotators exist but never co-annotate a unit.
    annotations = {
        "u1": {"a": "1"},
        "u2": {"b": "0"},
        "u3": {"c": "1"},
    }
    result = alt_test(annotations, {"u1": "1", "u2": "0", "u3": "1"})
    assert result.status == INSUFFICIENT_DATA


def test_super_human_model_passes() -> None:
    # Strong model vs four noisy humans: the model should beat every one.
    annotations, model_preds = _panel_data(
        n_units=150,
        annotator_accuracy=[0.75, 0.75, 0.75, 0.8],
        model_accuracy=0.95,
        seed=42,
    )
    result = alt_test(annotations, model_preds, margin=0.1)
    assert result.status == PASS
    assert result.winning_rate == 1.0
    assert result.advantage_probability is not None
    assert result.advantage_probability > 0.6
    assert all(v.rejected for v in result.verdicts)


def test_random_model_fails() -> None:
    rng = np.random.default_rng(9)
    annotations, _ = _panel_data(
        n_units=150,
        annotator_accuracy=[0.8, 0.8, 0.8],
        model_accuracy=0.5,  # pure noise vs the panel
        seed=1,
    )
    # Replace model labels with independent coin flips (definitely not aligned).
    model_preds = {u: str(rng.integers(0, 2)) for u in annotations}
    result = alt_test(annotations, model_preds, margin=0.1)
    assert result.status == FAIL
    assert result.winning_rate == 0.0
    assert not any(v.rejected for v in result.verdicts)


def test_margin_behavior_monotone() -> None:
    # The margin epsilon is the paper's cost-benefit DISCOUNT on human
    # annotation (H0: rho_f <= rho_h - eps): a larger margin demands LESS
    # of the model, so the winning rate is monotone NON-DECREASING in eps.
    annotations, model_preds = _panel_data(
        n_units=200,
        annotator_accuracy=[0.7, 0.75, 0.7, 0.75],
        model_accuracy=0.85,
        seed=17,
    )
    rates = [
        alt_test(annotations, model_preds, margin=m).winning_rate
        for m in (0.0, 0.05, 0.1, 0.2, 0.3)
    ]
    assert all(r is not None for r in rates)
    for smaller, larger in pairwise(rates):
        assert smaller is not None and larger is not None
        assert larger >= smaller  # a bigger human discount never lowers omega


def test_bootstrap_method_agrees_and_is_seeded() -> None:
    annotations, model_preds = _panel_data(
        n_units=120,
        annotator_accuracy=[0.75, 0.75, 0.75],
        model_accuracy=0.95,
        seed=3,
    )
    a = alt_test(annotations, model_preds, margin=0.1, method="bootstrap", n_bootstrap=500, seed=11)
    b = alt_test(annotations, model_preds, margin=0.1, method="bootstrap", n_bootstrap=500, seed=11)
    assert a.status == PASS
    assert a.winning_rate == b.winning_rate
    assert [v.p_value for v in a.verdicts] == [v.p_value for v in b.verdicts]


def test_exact_binomial_pvalues_valid_and_deterministic() -> None:
    annotations, model_preds = _panel_data(
        n_units=100,
        annotator_accuracy=[0.8, 0.8, 0.8],
        model_accuracy=0.95,
        seed=5,
    )
    result = alt_test(annotations, model_preds, margin=0.1)
    again = alt_test(annotations, model_preds, margin=0.1)
    assert result.status == PASS
    for v in result.verdicts:
        assert 0.0 <= v.p_value <= 1.0
    assert [v.p_value for v in result.verdicts] == [v.p_value for v in again.verdicts]


def test_unknown_method_raises() -> None:
    annotations, model_preds = _panel_data(20, [0.9, 0.9, 0.9], 0.9, seed=2)
    with pytest.raises(ValueError, match="method"):
        alt_test(annotations, model_preds, method="chi2")


def test_missing_model_prediction_scores_zero_not_dropped() -> None:
    # Model answers only half the units; the rest score S(f)=0 (worst case)
    # but stay in the test so the instance count reflects reality.
    annotations, model_preds = _panel_data(
        n_units=100,
        annotator_accuracy=[0.8, 0.8, 0.8],
        model_accuracy=0.95,
        seed=21,
    )
    partial = dict(list(model_preds.items())[:50])
    result = alt_test(annotations, partial, margin=0.1)
    assert result.n_instances == 100
    # Half the coverage makes winning harder but a strong model still wins.
    assert result.advantage_probability is not None
    assert result.advantage_probability < 1.0


def test_ties_count_for_both_sides() -> None:
    # Model perfectly equals the panel majority everywhere; humans disagree
    # with each other. Every instance is then either a strict model win
    # (d=-1) or a tie (d=0) -- never a strict human win.
    annotations, model_preds = _panel_data(
        n_units=80,
        annotator_accuracy=[0.7, 0.7, 0.7],
        model_accuracy=1.0,  # equals the planted truth = the 3-annotator majority
        seed=8,
    )
    result = alt_test(annotations, model_preds, margin=0.1)
    for v in result.verdicts:
        assert v.rho_f >= v.rho_h
    assert result.status == PASS


def test_by_correction_prevents_weak_rejections() -> None:
    # A mediocre model barely better than chance should not flip annotators
    # to "rejected" under the BY-corrected procedure.
    annotations, model_preds = _panel_data(
        n_units=60,
        annotator_accuracy=[0.85, 0.85, 0.85],
        model_accuracy=0.55,
        seed=31,
    )
    result = alt_test(annotations, model_preds, margin=0.1)
    assert result.status == FAIL
