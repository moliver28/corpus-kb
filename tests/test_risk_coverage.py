"""Known-truth tests for risk-coverage thresholding + Clopper-Pearson (U41)."""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
import pytest

from corpus_kb.coding.risk_coverage import (
    INSUFFICIENT_DATA,
    OK,
    binomial_cdf,
    clopper_pearson_upper,
    risk_coverage_curve,
    threshold_for_target_risk,
)


def test_binomial_cdf_known_values() -> None:
    assert binomial_cdf(0, 5, 0.5) == pytest.approx(1 / 32)
    assert binomial_cdf(5, 5, 0.5) == 1.0
    assert binomial_cdf(2, 10, 0.5) == pytest.approx(sum(math.comb(10, k) for k in range(3)) / 1024)
    assert binomial_cdf(-1, 10, 0.5) == 0.0
    assert binomial_cdf(0, 0, 0.5) == 1.0
    # p=0: everything at 0 is certain; p=1: any k<n is impossible.
    assert binomial_cdf(3, 10, 0.0) == 1.0
    assert binomial_cdf(3, 10, 1.0) == 0.0


def test_clopper_pearson_edge_cases_exact() -> None:
    # k = 0, n = 10, one-sided 95%: (1-U)^10 = 0.05 => U = 1 - 0.05^(1/10).
    expected = 1.0 - 0.05 ** (1.0 / 10.0)
    assert clopper_pearson_upper(0, 10, 0.95) == pytest.approx(expected, abs=1e-9)
    # k = n: no upper bound below 1.
    assert clopper_pearson_upper(10, 10, 0.95) == 1.0
    # n = 0: no data, bound is certainty.
    assert clopper_pearson_upper(0, 0, 0.95) == 1.0
    with pytest.raises(ValueError, match="confidence"):
        clopper_pearson_upper(0, 10, 1.5)


def test_clopper_pearson_monotone_and_guarantee_holds() -> None:
    ups = [clopper_pearson_upper(k, 100, 0.95) for k in range(0, 101, 10)]
    assert all(b >= a for a, b in pairwise(ups))
    # The bound is a real guarantee: P(X <= k | Binom(n, U)) <= 1 - c.
    for k in (0, 5, 15):
        u = clopper_pearson_upper(k, 100, 0.95)
        assert binomial_cdf(k, 100, u) <= 0.05 + 1e-9


def test_risk_coverage_curve_shape() -> None:
    # Scores descending; errors planted at the low-score tail.
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
    outcomes = np.array([0, 0, 0, 0, 0, 0, 0, 0, 1, 1])
    curve = risk_coverage_curve(scores, outcomes)
    assert len(curve.thresholds) == 10
    assert np.all(np.diff(curve.coverage) > 0)  # coverage strictly grows
    # Selective risk stays 0 until the erroneous units are accepted.
    assert curve.risk[0] == 0.0
    assert curve.risk[-1] == pytest.approx(0.2)  # full coverage = overall error rate
    # The accepted counts match the coverage fractions.
    assert curve.n_accepted[-1] == 10


def test_risk_coverage_curve_empty_and_misaligned() -> None:
    empty = risk_coverage_curve(np.array([]), np.array([]))
    assert len(empty.thresholds) == 0
    with pytest.raises(ValueError, match="align"):
        risk_coverage_curve(np.array([0.5]), np.array([0, 1]))


def test_threshold_attains_target_on_separable_data() -> None:
    rng = np.random.default_rng(0)
    scores_cal = np.concatenate([rng.uniform(0.6, 1.0, 300), rng.uniform(0.0, 0.5, 100)])
    outcomes_cal = np.concatenate([np.zeros(300), np.ones(100)])
    scores_test = np.concatenate([rng.uniform(0.6, 1.0, 300), rng.uniform(0.0, 0.5, 100)])
    outcomes_test = np.concatenate([np.zeros(300), np.ones(100)])
    result = threshold_for_target_risk(
        scores_cal,
        outcomes_cal,
        scores_test,
        outcomes_test,
        target_risk=0.05,
        min_calibration_n=30,
    )
    assert result.status == OK
    assert result.threshold is not None
    # The threshold maximizes coverage under the exact CP bound: it may dip
    # below the clean gap when the thin top of the noisy band keeps the
    # bounded risk inside the target, so assert the GUARANTEE, not a guess.
    assert result.cp_upper_bound is not None and result.cp_upper_bound <= 0.05
    assert result.test_risk is not None and result.test_risk <= 0.05
    assert result.test_coverage is not None and result.test_coverage >= 0.7


def test_insufficient_calibration_data_routes_everything_to_review() -> None:
    result = threshold_for_target_risk(
        np.array([0.9, 0.8, 0.7]),
        np.array([0, 0, 1]),
        np.array([0.9]),
        np.array([0]),
        min_calibration_n=30,
    )
    assert result.status == INSUFFICIENT_DATA
    assert result.threshold is None  # sentinel: route everything to review
    assert "routing everything to review" in result.reason


def test_unattainable_target_reports_no_threshold() -> None:
    rng = np.random.default_rng(4)
    n = 100
    # Every unit is an error regardless of score -> no threshold can help.
    scores = rng.random(n)
    outcomes = np.ones(n)
    result = threshold_for_target_risk(
        scores, outcomes, scores, outcomes, target_risk=0.05, min_calibration_n=30
    )
    assert result.status == OK
    assert result.threshold is None
    assert "no threshold" in result.reason


def test_realized_test_risk_near_nominal() -> None:
    # 5% planted error rate among accepted units; target 10% with slack.
    rng = np.random.default_rng(12)
    n = 1000
    scores = rng.random(n)
    outcomes = (rng.random(n) < 0.05).astype(float)
    result = threshold_for_target_risk(
        scores, outcomes, scores, outcomes, target_risk=0.10, min_calibration_n=100
    )
    assert result.status == OK
    assert result.threshold is not None
    assert result.test_risk is not None
    assert result.test_risk <= 0.10
    assert result.cp_upper_bound is not None and result.cp_upper_bound <= 0.10
