"""Graded cells (TD-07): U/A/B/C beside the route; reflexive never auto-accepts (AM-07)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "GRADE_REASONS",
    "GradeResult",
    "GradeSignals",
    "grade_cell",
    "grade_conformal",
    "reflexive_route",
]

Grade = Literal["A", "B", "C", "U"]

#: The closed reason list; the migration CHECK enforces exactly this set (D-10).
GRADE_REASONS: tuple[str, ...] = (
    "insufficient_calibration_seeds",
    "signal_not_measured",
    "quote_not_verified",
    "reflexive_paradigm",
    "eligible_auto_accept",
    "error_bound_above_target",
    "conformal_set_not_singleton",
    "escalated_next_rung",
    "signals_contradict_decision",
    "structure_canary_failed",
    "translation_quality_failed",
)

#: Translation-gate states (refine D-4 truth table).
TRANSLATION_UNTRANSLATED = "untranslated"
TRANSLATION_OK = "ok"
TRANSLATION_BELOW_FLOOR = "below_floor"
TRANSLATION_NOT_MEASURED = "not_measured"


@dataclass(frozen=True)
class GradeSignals:
    decision: str = "assign"
    paradigm: str | None = None
    quote_ok: bool = True
    calibration_n: int | None = None
    min_calibration_n: int = 30
    conformal_singleton: bool | None = None
    error_bound: float | None = None
    target_risk: float = 0.05
    translation_state: str = TRANSLATION_UNTRANSLATED
    signals_contradict: bool = False
    structure_canary_ok: bool = True


@dataclass(frozen=True)
class GradeResult:
    grade: Grade
    reason: str


def grade_cell(signals: GradeSignals) -> GradeResult:
    """One cell's grade per the TD-07 rule order."""
    if signals.paradigm == "reflexive":
        return GradeResult("U", "reflexive_paradigm")
    # TD-07 point 3: the quote gate is one of the four A conditions, for
    # every decision — an unverified quote fails rejects too (AM-09).
    if not signals.quote_ok:
        return GradeResult("U", "quote_not_verified")
    if not signals.structure_canary_ok:
        return GradeResult("C", "structure_canary_failed")
    if signals.signals_contradict:
        return GradeResult("C", "signals_contradict_decision")
    if signals.calibration_n is None or signals.calibration_n < signals.min_calibration_n:
        return GradeResult("U", "insufficient_calibration_seeds")
    if signals.conformal_singleton is False:
        return GradeResult("B", "conformal_set_not_singleton")
    if signals.conformal_singleton is None:
        return GradeResult("U", "signal_not_measured")
    if signals.translation_state == TRANSLATION_NOT_MEASURED:
        return GradeResult("U", "signal_not_measured")
    if signals.error_bound is None:
        return GradeResult("U", "signal_not_measured")
    if signals.conformal_singleton and signals.error_bound <= signals.target_risk:
        if signals.translation_state == TRANSLATION_BELOW_FLOOR:
            # D-4 implements the grade-side cap only: below floor grades B.
            # The route consequence lands with grade-aware routing (T2).
            return GradeResult("B", "translation_quality_failed")
        return GradeResult("A", "eligible_auto_accept")
    return GradeResult("B", "error_bound_above_target")


def grade_conformal(set_size: int | None) -> GradeResult:
    """Grade from a conformal set size alone (the deductive path's seam)."""
    if set_size is None:
        return GradeResult("U", "signal_not_measured")
    if set_size > 1:
        return GradeResult("B", "conformal_set_not_singleton")
    return GradeResult("U", "signal_not_measured")


_ACCEPT_ROUTES = {"high-conf-accept", "high-conf-reject"}


def reflexive_route(route: str) -> str:
    """Reflexive law (AM-07): no route ends in an accept without a human."""
    if route in _ACCEPT_ROUTES:
        return "manual_review"
    return route
