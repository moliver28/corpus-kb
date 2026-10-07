"""Offline tests for research.deductive decision rules (todo 14)."""

from __future__ import annotations

from corpus_kb.research.deductive import decide_code, decide_unit


def _th(tau_a: float, tau_qa: float, delta: float, unreliable: bool = False) -> dict[str, object]:
    return {
        "tau_a": tau_a,
        "tau_qa": tau_qa,
        "delta": delta,
        "m_gz_a": 0.05,
        "m_gz_qa": 0.05,
        "m_gz_delta": 0.02,
        "unreliable": unreliable,
    }


def test_explicit_decision_auto():
    d = decide_code(
        "c",
        s_a=0.9,
        s_qa=0.4,
        s_q=0.3,
        thresholds=_th(0.8, 0.8, 0.1),
        stance="affirm",
        is_interpretive=False,
    )
    assert d is not None
    assert d.evidence_basis == "explicit_in_answer"
    assert d.status == "auto"
    assert d.reason == "explicit"


def test_question_dependent_decision_review():
    d = decide_code(
        "c",
        s_a=0.5,
        s_qa=0.9,
        s_q=0.3,
        thresholds=_th(0.8, 0.8, 0.5),
        stance="partial",
        is_interpretive=False,
    )
    assert d is not None
    assert d.evidence_basis == "question_dependent"
    assert d.status == "review"
    assert d.reason == "question_dependent"


def test_deny_deflect_never_question_dependent():
    d = decide_code(
        "c",
        s_a=0.5,
        s_qa=0.9,
        s_q=0.3,
        thresholds=_th(0.8, 0.8, 0.5),
        stance="deny",
        is_interpretive=False,
    )
    if d is not None:
        assert d.evidence_basis != "question_dependent"


def test_deny_deflect_routes_review_when_close():
    # 0.71 is below the gray-zone band [0.75, 0.8) but inside the
    # deny/deflect "close" window (tau - 2*m_gz = 0.7).
    d = decide_code(
        "c",
        s_a=0.71,
        s_qa=0.4,
        s_q=0.3,
        thresholds=_th(0.8, 0.8, 0.1),
        stance="deny",
        is_interpretive=False,
    )
    assert d is not None
    assert d.status == "review"
    assert d.reason == "deny_deflect"


def test_question_echo_not_question_dependent():
    # qa and question scores are nearly identical -> echo.
    d = decide_code(
        "c",
        s_a=0.5,
        s_qa=0.92,
        s_q=0.91,
        thresholds=_th(0.8, 0.8, 0.1),
        stance="affirm",
        is_interpretive=False,
    )
    assert d is None or d.evidence_basis != "question_dependent"


def test_q_alone_never_assigns():
    d = decide_code(
        "c",
        s_a=0.3,
        s_qa=0.3,
        s_q=0.95,
        thresholds=_th(0.5, 0.5, 0.1),
        stance="affirm",
        is_interpretive=False,
    )
    assert d is None


def test_interpretive_routes_to_review():
    d = decide_code(
        "c",
        s_a=0.99,
        s_qa=0.99,
        s_q=0.99,
        thresholds=_th(0.5, 0.5, 0.1),
        stance="affirm",
        is_interpretive=True,
    )
    assert d is not None
    assert d.status == "review"
    assert d.reason == "interpretive"


def test_gray_zone_routes_to_review():
    d = decide_code(
        "c",
        s_a=0.79,
        s_qa=0.3,
        s_q=0.2,
        thresholds=_th(0.8, 0.8, 0.1),
        stance="affirm",
        is_interpretive=False,
    )
    assert d is not None
    assert d.status == "review"
    assert d.reason == "gray_zone"


def test_gray_zone_with_ambiguous_conformal_set_routes_conformal():
    d = decide_code(
        "c",
        s_a=0.79,
        s_qa=0.3,
        s_q=0.2,
        thresholds=_th(0.8, 0.8, 0.1),
        stance="affirm",
        is_interpretive=False,
        conformal_set_size=2,
    )
    assert d is not None
    assert d.status == "review"
    assert d.reason == "conformal"


def test_gray_zone_with_singleton_conformal_set_keeps_gray_zone():
    d = decide_code(
        "c",
        s_a=0.79,
        s_qa=0.3,
        s_q=0.2,
        thresholds=_th(0.8, 0.8, 0.1),
        stance="affirm",
        is_interpretive=False,
        conformal_set_size=1,
    )
    assert d is not None
    assert d.status == "review"
    assert d.reason == "gray_zone"


def test_conformal_never_overrides_tau_decision():
    # Set-size 3 would be ambiguous, but the explicit tau rule already fired:
    # conformal is a routing trigger only, never an override (r11 layering).
    d = decide_code(
        "c",
        s_a=0.9,
        s_qa=0.4,
        s_q=0.3,
        thresholds=_th(0.8, 0.8, 0.1),
        stance="affirm",
        is_interpretive=False,
        conformal_set_size=3,
    )
    assert d is not None
    assert d.status == "auto"
    assert d.reason == "explicit"


def test_unreliable_threshold_routes_to_review():
    d = decide_code(
        "c",
        s_a=0.99,
        s_qa=0.99,
        s_q=0.99,
        thresholds=_th(0.5, 0.5, 0.1, unreliable=True),
        stance="affirm",
        is_interpretive=False,
    )
    assert d is not None
    assert d.status == "review"
    assert d.reason == "threshold_unreliable"


def test_decide_unit_multi_code():
    scores = {
        "c1": {"answer": 0.9, "qa": 0.4, "question": 0.3},
        "c2": {"answer": 0.3, "qa": 0.85, "question": 0.3},
    }
    thresholds = {
        "c1": _th(0.8, 0.8, 0.1),
        "c2": _th(0.8, 0.8, 0.5),
    }
    decisions = decide_unit(scores, thresholds, stance="affirm")
    by_code = {d.code_id: d for d in decisions}
    assert by_code["c1"].evidence_basis == "explicit_in_answer"
    assert by_code["c2"].evidence_basis == "question_dependent"
