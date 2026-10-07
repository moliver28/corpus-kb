"""Offline tests for research.deductive_run glue (todo 14) — no DB needed."""

from __future__ import annotations

import numpy as np

from corpus_kb.research.deductive_run import (
    _coverage_of,
    _is_uuid,
    _loo_calibration_rows,
    _score_view,
    _thresholds_from_theory,
)
from corpus_kb.research.run_inputs import UnitViews


def _unit(seed: int, dim: int = 16) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tolist()


def test_is_uuid():
    assert _is_uuid("00000000-0000-0000-0000-000000000001")
    assert not _is_uuid("onboarding_pain")
    assert not _is_uuid("")


def test_thresholds_from_theory_defaults_unreliable():
    assert _thresholds_from_theory({})["unreliable"] is True
    t = _thresholds_from_theory(
        {
            "thresholds": {
                "tau_a": 0.6,
                "tau_qa": 0.7,
                "delta": 0.05,
                "threshold_unreliable": False,
                "cv_range": {"m_gz_a": 0.03, "m_gz_qa": 0.02, "m_gz_delta": 0.01},
            }
        }
    )
    assert t["tau_a"] == 0.6
    assert t["m_gz_a"] == 0.03
    assert t["unreliable"] is False


def test_score_view_streams_max_prototype_scores():
    units = [
        UnitViews(1, _unit(10), _unit(11), _unit(12), None, None),
        UnitViews(2, _unit(99), _unit(98), _unit(97), None, None),
    ]
    scores = _score_view(units, "answer", [_unit(10), _unit(20)])
    assert len(scores) == 2
    assert scores[0] > 0.95
    assert scores[1] < 0.9


def test_loo_calibration_rows_shapes_and_labels():
    gold = {
        "c1": [(_unit(i), _unit(i + 50), _unit(i + 100)) for i in range(4)],
        "c2": [(_unit(i + 200), _unit(i + 250), _unit(i + 300)) for i in range(3)],
    }
    scores, labels = _loo_calibration_rows(gold, ["c1", "c2"])
    assert scores.shape == (7, 2)
    assert labels.shape == (7, 2)
    assert int(labels.sum()) == 7
    # Row i is labeled for the code that owns the exemplar.
    assert labels[0][0] == 1 and labels[0][1] == 0
    assert labels[4][0] == 0 and labels[4][1] == 1


def test_coverage_of_counts_units_with_basis():
    class D:
        def __init__(self, basis: str | None) -> None:
            self.evidence_basis = basis

    decisions = {
        1: [D("explicit_in_answer")],
        2: [D("question_dependent")],
        3: [],
    }
    assert _coverage_of(decisions, "explicit_in_answer") == 1 / 3
    assert _coverage_of(decisions, "question_dependent") == 1 / 3
    assert _coverage_of({}, "explicit_in_answer") == 0.0
