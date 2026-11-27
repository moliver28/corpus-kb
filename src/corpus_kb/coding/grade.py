"""Graded cells (TD-07, TD-08): one U/A/B/C grade per coded cell.

The grade is a summary beside the existing route; it never replaces the
route (athena G-01: this module adds the new signals on top of
``route_for_review``, it does not restate the router). Rules, in order:

- **U, undetermined**: the ``reflexive`` paradigm (TD-08), an assign whose
  quote failed the verbatim gate, fewer than ``routing.min_calibration_n``
  seeds, a required signal not measured, or translation quality not
  measured on a translated unit. U never auto-accepts.
- **A**: the conformal set is a singleton, the error bound is at or below
  the target risk, the quote gate passed, and the translation gate passed
  when the unit is translated.
- **B, tentative**: evidence exists and is not enough for A — the
  conformal set has more than one label, the error bound is above the
  target, or quality sits below the floor (D-4 caps at B and routes to
  review).
- **C, not accepted**: the signals contradict the decision, or the
  structure canary failed.

The ``grade_reason`` closed list is the migration CHECK list (D-10). The
AM-07 pin lives here: ``reflexive_route`` maps every accept-ending route
to ``manual_review``, so no route ends in an accept without a human.
Every number is an owned convention until a seed set shows it (C12).
"""

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

#: The closed reason list; the migration CHECK enforces exactly this set.
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
    """Every signal available for one coded cell; absent ones stay None."""

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
    """One grade plus its reason from the closed list."""

    grade: Grade
    reason: str


def grade_cell(signals: GradeSignals) -> GradeResult:
    """One cell's grade per the TD-07 rules, in the documented order."""
    if signals.paradigm == "reflexive":
        return GradeResult("U", "reflexive_paradigm")
    if signals.decision == "assign" and not signals.quote_ok:
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
            return GradeResult("B", "translation_quality_failed")
        return GradeResult("A", "eligible_auto_accept")
    return GradeResult("B", "error_bound_above_target")


def grade_conformal(set_size: int | None) -> GradeResult:
    """The grade from a conformal set size alone (the deductive path's seam).

    A measured multi-label set is B (evidence exists, A is impossible); a
    singleton without the error bound is U (a required signal is not
    measured); no set at all is U.
    """
    if set_size is None:
        return GradeResult("U", "signal_not_measured")
    if set_size > 1:
        return GradeResult("B", "conformal_set_not_singleton")
    return GradeResult("U", "signal_not_measured")


_ACCEPT_ROUTES = {"high-conf-accept", "high-conf-reject"}


def reflexive_route(route: str) -> str:
    """TD-08: in the reflexive paradigm no route ends in an accept.

    Both accept-ending routes land in manual review; the review routes
    keep their names (the existing route names do not change).
    """
    if route in _ACCEPT_ROUTES:
        return "manual_review"
    return route
