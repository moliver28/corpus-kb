"""Deductive decision layering (todo 14, v5 §8).

Rules enforced:
  * explicit evidence: s_a >= tau_a
  * question-dependent evidence: s_qa >= tau_qa AND s_qa - s_q >= delta
    AND stance in {affirm, partial} AND not a question echo
  * deny/deflect NEVER question-dependent-assigned
  * Q-alone NEVER assigns
  * interpretive codes and threshold-unreliable codes route to review
  * gray-zone band [tau - m_gz, tau) routes to review; when the conformal
    prediction set for the unit has size > 1 the routing reason is
    ``conformal`` instead of ``gray_zone`` (conformal is a ROUTING trigger
    only — it never overrides the tau decision rule)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class DeductiveDecision:
    """One (unit, code) decision from the deductive layer."""

    code_id: str
    evidence_basis: str | None
    confidence: str
    tier_fired: int
    status: str
    reason: str


# Stances that permit question-dependent assignment (v5 §8).
_QUESTION_DEPENDENT_STANCES = {"affirm", "partial"}
_DENY_DEFLECT_STANCES = {"deny", "deflect"}


def _in_gray_zone(score: float, tau: float, m_gz: float) -> bool:
    return tau - m_gz <= score < tau


def _confidence_level(score: float, tau: float, m_gz: float) -> str:
    if score >= tau + m_gz:
        return "high"
    if score >= tau:
        return "medium"
    return "low"


def _gray_zone_reason(conformal_set_size: int | None) -> str:
    """Gray zone routes to review; ambiguous conformal sets label the reason.

    Layering (todo 14, r11): the tau rule still owns the DECISION (review);
    the conformal set-size only annotates WHY, and only when it is > 1.
    """
    if conformal_set_size is not None and conformal_set_size > 1:
        return "conformal"
    return "gray_zone"


def decide_code(
    code_id: str,
    s_a: float,
    s_qa: float,
    s_q: float,
    thresholds: dict[str, object],
    stance: str | None,
    is_interpretive: bool,
    conformal_set_size: int | None = None,
) -> DeductiveDecision | None:
    """Return a decision for one (unit, code) or None if no signal fires.

    Args:
        code_id: Code identifier.
        s_a: Answer-view max score.
        s_qa: QA-view max score.
        s_q: Question-view max score.
        thresholds: Dict with tau_a, tau_qa, delta, m_gz_a, m_gz_qa, m_gz_delta,
            and unreliable bool.
        stance: Unit stance (affirm/deny/partial/deflect/n/a or None).
        is_interpretive: Whether this code is interpretive/abstract.
        conformal_set_size: Size of the unit's conformal prediction set, when
            computed. >1 inside the gray zone routes with reason=conformal.

    Returns:
        DeductiveDecision or None when the code clearly does not apply.
    """
    if is_interpretive:
        return DeductiveDecision(
            code_id=code_id,
            evidence_basis=None,
            confidence="low",
            tier_fired=0,
            status="review",
            reason="interpretive",
        )

    if bool(thresholds.get("unreliable", False)):
        return DeductiveDecision(
            code_id=code_id,
            evidence_basis=None,
            confidence="low",
            tier_fired=0,
            status="review",
            reason="threshold_unreliable",
        )

    tau_a = float(cast(float, thresholds.get("tau_a", 0.0)))
    tau_qa = float(cast(float, thresholds.get("tau_qa", 0.0)))
    delta = float(cast(float, thresholds.get("delta", 0.0)))
    m_gz_a = float(cast(float, thresholds.get("m_gz_a", 0.0)))
    m_gz_qa = float(cast(float, thresholds.get("m_gz_qa", 0.0)))

    # Explicit evidence first.
    if s_a >= tau_a:
        return DeductiveDecision(
            code_id=code_id,
            evidence_basis="explicit_in_answer",
            confidence=_confidence_level(s_a, tau_a, m_gz_a),
            tier_fired=0,
            status="auto",
            reason="explicit",
        )

    # Gray zone on answer view routes to review before attempting qdep.
    if _in_gray_zone(s_a, tau_a, m_gz_a):
        return DeductiveDecision(
            code_id=code_id,
            evidence_basis=None,
            confidence="low",
            tier_fired=0,
            status="review",
            reason=_gray_zone_reason(conformal_set_size),
        )

    # Question-dependent evidence.
    gain = s_qa - s_q
    qdep_eligible = (
        s_qa >= tau_qa
        and gain >= delta
        and stance in _QUESTION_DEPENDENT_STANCES
        and not _is_echo(s_qa, s_q)
    )
    if qdep_eligible:
        return DeductiveDecision(
            code_id=code_id,
            evidence_basis="question_dependent",
            confidence="medium",
            tier_fired=0,
            status="review",
            reason="question_dependent",
        )

    if _in_gray_zone(s_qa, tau_qa, m_gz_qa):
        return DeductiveDecision(
            code_id=code_id,
            evidence_basis=None,
            confidence="low",
            tier_fired=0,
            status="review",
            reason=_gray_zone_reason(conformal_set_size),
        )

    # Deny/deflect stance: never silently reject; surface for review when close.
    close = s_a >= tau_a - m_gz_a * 2 or s_qa >= tau_qa - m_gz_qa * 2
    if stance in _DENY_DEFLECT_STANCES and close:
        return DeductiveDecision(
            code_id=code_id,
            evidence_basis=None,
            confidence="low",
            tier_fired=0,
            status="review",
            reason="deny_deflect",
        )

    return None


def _is_echo(s_qa: float, s_q: float, tol: float = 0.02) -> bool:
    """Question echo: QA score is almost identical to Q score.

    A unit whose content merely echoes the question has high s_qa because the
    QA span is dominated by the question text, but no answer-specific signal.
    The gain guard catches this via s_qa - s_q < delta; this helper is an
    explicit safety net.
    """
    return abs(s_qa - s_q) <= tol


def decide_unit(
    scores_by_code: dict[str, dict[str, float]],
    thresholds_by_code: dict[str, dict[str, object]],
    stance: str | None,
    interpretive_codes: set[str] | None = None,
    conformal_set_size: int | None = None,
) -> list[DeductiveDecision]:
    """Decide every code for one unit.

    Args:
        scores_by_code: {code_id: {"answer": s_a, "qa": s_qa, "question": s_q}}.
        thresholds_by_code: {code_id: thresholds dict}.
        stance: Unit stance.
        interpretive_codes: Set of code_ids that are interpretive.
        conformal_set_size: Unit-level conformal prediction set size, when
            computed (>1 inside the gray zone routes with reason=conformal).

    Returns:
        List of DeductiveDecision (one per accepted/review code).
    """
    interpretive = interpretive_codes or set()
    decisions: list[DeductiveDecision] = []
    for code_id, scores in scores_by_code.items():
        thresholds = thresholds_by_code.get(code_id, {})
        decision = decide_code(
            code_id=code_id,
            s_a=float(scores.get("answer", 0.0)),
            s_qa=float(scores.get("qa", 0.0)),
            s_q=float(scores.get("question", 0.0)),
            thresholds=thresholds,
            stance=stance,
            is_interpretive=code_id in interpretive,
            conformal_set_size=conformal_set_size,
        )
        if decision is not None:
            decisions.append(decision)
    return decisions
