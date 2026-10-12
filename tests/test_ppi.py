"""Known-truth tests for the prediction-powered estimator (U14)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.coding.ppi import CORRECTED, INSUFFICIENT_DATA, UNCORRECTED, ppi_estimate


def test_no_audit_sample_reports_uncorrected() -> None:
    model = np.array([0.4, 0.6, 0.5, 0.7])
    result = ppi_estimate(model, np.array([]), np.array([]), np.array([]))
    assert result.status == UNCORRECTED
    assert result.label == UNCORRECTED
    assert result.estimate == result.raw_estimate == float(np.mean(model))
    assert result.lower is None and result.upper is None
    assert result.n_audit == 0


def test_unbiased_model_correction_is_small_and_ci_contains_truth() -> None:
    rng = np.random.default_rng(0)
    n_pop = 4000
    truth = np.full(n_pop, 0.3)
    # Model unbiased: predictions scatter around the truth symmetrically.
    model = np.clip(truth + rng.normal(0, 0.05, n_pop), 0.0, 1.0)
    audit_idx = rng.choice(n_pop, size=200, replace=False)
    human = truth[audit_idx] + rng.normal(0, 0.02, 200)
    result = ppi_estimate(
        model,
        human,
        model[audit_idx],
        np.full(200, 200 / n_pop),
    )
    assert result.status == CORRECTED
    assert result.label == CORRECTED
    assert result.estimate == pytest.approx(0.3, abs=0.01)
    assert result.lower is not None and result.upper is not None
    assert result.lower < 0.3 < result.upper
    # The interval is tight: unbiased model + honest audit -> narrow CI.
    assert result.upper - result.lower < 0.08


def test_biased_model_correction_moves_toward_truth() -> None:
    rng = np.random.default_rng(1)
    n_pop = 3000
    truth = np.full(n_pop, 0.5)
    # Model over-predicts by +0.1 on average.
    model = np.clip(truth + 0.1 + rng.normal(0, 0.03, n_pop), 0.0, 1.0)
    audit_idx = rng.choice(n_pop, size=300, replace=False)
    human = truth[audit_idx]  # humans are exactly right on the audit
    pi = np.full(300, 300 / n_pop)
    result = ppi_estimate(model, human, model[audit_idx], pi)
    assert result.raw_estimate > 0.58  # raw is visibly biased high
    assert result.estimate < result.raw_estimate  # correction pulls back
    assert result.estimate == pytest.approx(0.5, abs=0.02)
    assert result.lower is not None and result.upper is not None
    assert result.lower < 0.5 < result.upper


def test_inverse_probability_weights_reweight_strata() -> None:
    # Population: 900 units at 0 and 100 at 1 (true mean 0.1); the model is
    # EXACT on every unit, so the naive raw estimate is already 0.1 and the
    # audit can only confirm it. To expose the reweighting, corrupt the
    # model on the oversampled stratum: the model says 0.2 on the 1-units.
    model = np.concatenate([np.zeros(900), np.full(100, 0.2)])
    # Audit: 20 zeros (pi = 20/900) + 20 ones (pi = 20/100, oversampled 9x).
    human = np.concatenate([np.zeros(20), np.ones(20)])
    model_audited = np.concatenate([np.zeros(20), np.full(20, 0.2)])
    pi = np.concatenate([np.full(20, 20 / 900), np.full(20, 20 / 100)])
    result = ppi_estimate(model, human, model_audited, pi)
    assert result.status == CORRECTED
    # Residuals: 0 on the zeros, +0.8 on the ones. IPW down-weights the
    # oversampled ones (w = 5) against the zeros (w = 45):
    # correction = (20*45*0 + 20*5*0.8)/(20*45 + 20*5) = 80/1000 = 0.08.
    # estimate = raw (0.02) + 0.08 = 0.10 = the truth.
    assert result.estimate == pytest.approx(0.1, abs=1e-9)
    # A naive unweighted correction would have given 0.4 -> estimate 0.42.
    assert result.effective_audit_n is not None
    assert result.effective_audit_n < 40  # Kish n_eff reflects the imbalance


def test_single_audit_unit_is_insufficient_data() -> None:
    model = np.array([0.5, 0.6, 0.4])
    result = ppi_estimate(model, np.array([1.0]), np.array([0.9]), np.array([1 / 3]))
    assert result.status == INSUFFICIENT_DATA
    assert result.estimate == result.raw_estimate
    assert result.lower is None and result.upper is None


def test_invalid_inputs_raise() -> None:
    model = np.array([0.5, 0.6])
    with pytest.raises(ValueError, match="align"):
        ppi_estimate(model, np.array([1.0, 0.0]), np.array([0.9]), np.array([0.5]))
    with pytest.raises(ValueError, match="inclusion"):
        ppi_estimate(model, np.array([1.0]), np.array([0.9]), np.array([0.0]))
    with pytest.raises(ValueError, match="non-empty"):
        ppi_estimate(np.array([]), np.array([]), np.array([]), np.array([]))


def test_estimator_is_deterministic() -> None:
    rng = np.random.default_rng(2)
    model = rng.random(500)
    audit = rng.random(50)
    model_audited = rng.random(50)
    pi = np.full(50, 0.1)
    a = ppi_estimate(model, audit, model_audited, pi)
    b = ppi_estimate(model, audit, model_audited, pi)
    assert a == b
