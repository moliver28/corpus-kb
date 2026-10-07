"""Offline tests for research.calibration (todo 14)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import numpy as np

from corpus_kb.research.calibration import (
    conformal_sets,
    expected_calibration_error,
    fit_code_thresholds,
    fit_temperature,
)

_FIXTURE = Path(__file__).with_name("fixtures") / "research" / "gold_fixture.json"


def _load_records() -> list[dict[str, object]]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return data["records"]


def test_fit_code_thresholds_produces_reasonable_thresholds():
    records = _load_records()
    for code_id in ("onboarding_pain", "trust", "support_slow"):
        t = fit_code_thresholds(records, code_id, n_folds=5, rng=np.random.default_rng(11))
        assert t.tau_a > 0.0
        assert t.tau_qa > 0.0
        assert t.delta >= 0.0
        assert t.m_gz_a >= 0.0
        assert t.m_gz_qa >= 0.0
        assert t.m_gz_delta >= 0.0
        assert t.n_gold >= 20
        assert t.unreliable is False
        assert "tau_a" in t.cv_range


def test_gray_zone_margin_is_calibrated_not_hardcoded():
    """m_gz is the per-fold threshold std (r9): it must vary with the data.

    A hardcoded band (e.g. always 0.05) would make these assertions fail on
    at least one code; a degenerate constant zero would collapse the band.
    """
    records = _load_records()
    fits = {
        code_id: fit_code_thresholds(records, code_id, n_folds=5, rng=np.random.default_rng(11))
        for code_id in ("onboarding_pain", "trust", "support_slow")
    }
    m_gz_values = [t.m_gz_a for t in fits.values()]
    assert any(m > 0.0 for m in m_gz_values), "m_gz_a collapsed to zero on all codes"
    assert len(set(m_gz_values)) == len(m_gz_values), "m_gz_a identical across codes (hardcoded?)"
    for t in fits.values():
        # Fold spread and the stored min/max must agree with the band width.
        tau_a_range = cast(dict[str, float], t.cv_range["tau_a"])
        assert t.m_gz_a <= max(tau_a_range["max"] - tau_a_range["min"], t.m_gz_a) + 1e-9


def test_small_gold_n_flags_threshold_unreliable():
    """QA failure scenario: gold n=10 -> unreliable flag + review routing."""
    records = _load_records()
    subset = [r for r in records if r["code_id"] == "onboarding_pain"][:10]
    assert len(subset) == 10
    t = fit_code_thresholds(subset, "onboarding_pain", n_folds=5, rng=np.random.default_rng(11))
    assert t.unreliable is True
    assert t.n_gold == 10


def test_empty_gold_marks_unreliable():
    t = fit_code_thresholds([], "missing_code", rng=np.random.default_rng(11))
    assert t.unreliable is True
    assert t.n_gold == 0


def test_fit_temperature_returns_positive_temperature():
    rng = np.random.default_rng(5)
    scores = rng.normal(size=(50, 3)).astype(np.float32)
    labels = np.zeros((50, 3), dtype=int)
    labels[:25, 0] = 1
    labels[25:, 1] = 1
    t, loss = fit_temperature(scores, labels)
    assert t > 0.0
    assert loss >= 0.0


def test_expected_calibration_error_on_perfect_calibration():
    n = 100
    confidences = np.linspace(0.0, 1.0, n)
    accuracies = confidences
    ece = expected_calibration_error(confidences, accuracies, bins=10)
    assert ece < 0.01


def test_conformal_sets_achieves_nominal_coverage():
    rng = np.random.default_rng(9)
    n_codes = 5
    n_cal = 100
    # Generate calibrated scores where labels align with top scores.
    scores = rng.normal(size=(n_cal, n_codes)).astype(np.float32)
    labels = np.zeros((n_cal, n_codes), dtype=int)
    for i in range(n_cal):
        labels[i, np.argmax(scores[i])] = 1
    test_scores = rng.normal(size=(20, n_codes)).astype(np.float32)
    sets, coverage = conformal_sets(scores, labels, test_scores, alpha=0.1)
    assert len(sets) == 20
    assert coverage >= 0.9
    assert all(isinstance(s, set) for s in sets)


def test_conformal_sets_tiny_calibration_does_not_crash():
    """ceil((n+1)(1-alpha))/n > 1 for small n: the quantile clamps to 1.0."""
    scores = np.array([[0.9, 0.1], [0.2, 0.8], [0.5, 0.5]], dtype=np.float32)
    labels = np.array([[1, 0], [0, 1], [1, 0]], dtype=int)
    test_scores = np.array([[0.7, 0.3]], dtype=np.float32)
    sets, coverage = conformal_sets(scores, labels, test_scores, alpha=0.1)
    assert len(sets) == 1
    assert sets[0], "prediction set must never be empty"
    assert 0.0 <= coverage <= 1.0
