"""Offline tests for the combined review priority + routing (todo 16)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.coding.uncertainty.cascade import assert_reasons_closed
from corpus_kb.coding.uncertainty.review_priority import (
    MIN_ITEMS_FOR_REFIT,
    REVIEW_REASONS,
    InsufficientReviewDataError,
    ReviewSignal,
    default_weights,
    fit_weights_l2,
    normalize_reason,
    priority_scores,
    rank_normalize,
    route_reasons,
    top2_margins,
)


def test_rank_normalize_orders_and_handles_ties_and_missing():
    ranks = rank_normalize([3.0, 1.0, 2.0, 1.0, None])
    assert ranks[0] == pytest.approx(1.0)
    assert ranks[2] == pytest.approx(2.0 / 3.0)
    assert ranks[1] == pytest.approx(ranks[3])
    assert ranks[4] == 0.0
    assert rank_normalize([]).size == 0
    assert (rank_normalize([None, None]) == 0.0).all()


def test_equal_weights_are_the_default():
    weights = default_weights()
    assert len(weights) == 7
    assert len(set(weights.values())) == 1


def test_ambiguous_cases_rank_above_clear_cases():
    ambiguous = ReviewSignal(margin=0.03, hedge=4, link_score=0.4, question_dependent=True)
    clear = ReviewSignal(margin=0.85, hedge=0, link_score=0.95, question_dependent=False)
    priority = priority_scores([clear, clear, ambiguous])
    assert priority[2] > priority[0]
    assert priority[0] == pytest.approx(priority[1])


def test_priority_uses_measured_se_only():
    escalated = ReviewSignal(margin=0.5, se=0.9)
    peer = ReviewSignal(margin=0.5)
    clear = ReviewSignal(margin=0.5, se=0.0)
    priority = priority_scores([escalated, peer, clear])
    assert priority[0] > priority[2]
    assert priority[1] == priority[2]


def test_top2_margins_and_overlap_routing():
    scores = np.array([[0.90, 0.86, 0.10], [0.90, 0.20, 0.10]])
    margins = top2_margins(scores)
    assert margins[0] == pytest.approx(0.04)
    assert margins[1] == pytest.approx(0.70)
    reasons = route_reasons(margins, delta_amb=0.05, decision_reasons=["explicit", "explicit"])
    assert reasons == ["overlap", "gray_zone"]
    single = top2_margins(np.array([[0.9]]))
    assert single[0] == np.inf


def test_reason_enum_is_closed_and_mapping_falls_back_to_gray_zone():
    assert frozenset({"overlap", "conformal", "interpretive", "gray_zone"}) == REVIEW_REASONS
    assert normalize_reason("conformal") == "conformal"
    assert normalize_reason("interpretive") == "interpretive"
    assert normalize_reason("overlap") == "overlap"
    for raw in ("threshold_unreliable", "question_dependent", "deny_deflect", "explicit"):
        assert normalize_reason(raw) == "gray_zone"
    assert_reasons_closed(["overlap", "conformal", "interpretive", "gray_zone"])
    with pytest.raises(ValueError):
        assert_reasons_closed(["question_dependent"])


def test_refit_requires_300_reviewed_items():
    with pytest.raises(InsufficientReviewDataError):
        fit_weights_l2(np.zeros((MIN_ITEMS_FOR_REFIT - 1, 7)), np.zeros(MIN_ITEMS_FOR_REFIT - 1))


def test_refit_learns_predictive_signal_and_stays_nonnegative():
    rng = np.random.default_rng(16)
    n = 400
    informative = rng.random(n)
    noise = rng.random(n)
    features = np.column_stack([informative] + [noise] * 5 + [rng.random(n)])
    logits = 6.0 * (informative - 0.5)
    labels = (rng.random(n) < 1.0 / (1.0 + np.exp(-logits))).astype(float)
    weights = fit_weights_l2(features, labels, l2=0.5, lr=0.5, iters=3000)
    assert set(weights) == set(default_weights())
    assert weights["margin"] == max(weights.values())
    assert all(w >= 0.0 for w in weights.values())


def test_refit_validates_feature_shape():
    with pytest.raises(ValueError):
        fit_weights_l2(np.zeros((400, 5)), np.zeros(400))
