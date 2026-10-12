"""Tests for the U35 judgment-distribution mean and U18 cross-run agreement."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.coding.uncertainty.cross_run_agreement import (
    cross_run_agreement,
    cross_run_agreement_batch,
)
from corpus_kb.coding.uncertainty.judgment_distribution import judgment_mean


def test_judgment_mean_used_when_enabled() -> None:
    result = judgment_mean([0.2, 0.4, 0.6], enabled=True)
    assert result.status == "ok"
    assert result.used_mean
    assert result.mean_value == pytest.approx(0.4)
    assert result.mode_value == pytest.approx(0.2)  # all tied -> lowest
    assert result.n_samples == 3


def test_judgment_mean_off_reports_mode_honestly() -> None:
    samples = [0.7, 0.7, 0.1]
    result = judgment_mean(samples, enabled=False)
    assert result.status == "ok"
    assert not result.used_mean
    assert result.mode_value == pytest.approx(0.7)
    assert result.mean_value == pytest.approx(0.7)  # falls back to the mode


def test_judgment_mean_empty_is_insufficient() -> None:
    result = judgment_mean([], enabled=True)
    assert result.status == "insufficient_data"
    assert result.mean_value is None and result.mode_value is None
    assert not result.used_mean


def test_judgment_mean_tie_breaks_deterministic() -> None:
    a = judgment_mean([0.5, 0.9, 0.9, 0.5], enabled=True)
    b = judgment_mean([0.5, 0.9, 0.9, 0.5], enabled=True)
    assert a == b
    assert a.mode_value == 0.5  # count tie resolved to the lowest value


def test_cross_run_agreement_full_and_zero() -> None:
    assert cross_run_agreement(["a", "a", "a"]).score == 1.0
    assert cross_run_agreement(["a", "b", "c"]).score == 0.0


def test_cross_run_agreement_partial() -> None:
    result = cross_run_agreement(["a", "a", "b"])
    # Agreeing pairs: (0,1) only -> 1/3.
    assert result.score == pytest.approx(1 / 3)
    assert result.n_pairs == 3
    assert result.n_agreeing == 1


def test_cross_run_agreement_fewer_than_two_runs_is_none() -> None:
    single = cross_run_agreement(["a"])
    assert single.score is None
    assert single.n_pairs == 0


def test_cross_run_agreement_missing_run_counts_as_disagreement() -> None:
    result = cross_run_agreement(["a", None, "a"])
    # (0,2) agree; None never agrees -> 1/3.
    assert result.score == pytest.approx(1 / 3)


def test_cross_run_agreement_batch_matches_per_unit() -> None:
    runs = [["a", "a"], ["a", "b"], ["x"]]
    scores = cross_run_agreement_batch(runs)
    assert scores.shape == (3,)
    assert scores[0] == 1.0
    assert scores[1] == 0.0
    assert np.isnan(scores[2])  # unmeasured, never fabricated


def test_scores_are_valid_uncertainty_signal_inputs() -> None:
    """The U18 score is ordinal input for U47: distinct values stay distinct."""
    runs = [["a", "a", "a"], ["a", "a", "b"], ["a", "b", "c"]]
    scores = cross_run_agreement_batch(runs)
    assert scores[0] > scores[1] > scores[2]
