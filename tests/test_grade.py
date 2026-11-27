"""Grades U/A/B/C (TD-07) and the reflexive law (TD-08 / AM-07 pin)."""

from __future__ import annotations

from dataclasses import replace

from corpus_kb.coding.grade import (
    GRADE_REASONS,
    GradeSignals,
    grade_cell,
    grade_conformal,
    reflexive_route,
)

# A fully-measured cell: calibration met, bound under target, singleton set.
FULL = GradeSignals(
    decision="assign",
    paradigm="codebook",
    quote_ok=True,
    calibration_n=72,
    min_calibration_n=30,
    conformal_singleton=True,
    error_bound=0.04,
    target_risk=0.05,
)


def test_grade_a_when_all_four_hold() -> None:
    graded = grade_cell(FULL)
    assert (graded.grade, graded.reason) == ("A", "eligible_auto_accept")


def test_reflexive_paradigm_forces_u_and_blocks_accept() -> None:
    graded = grade_cell(replace(FULL, paradigm="reflexive"))
    assert (graded.grade, graded.reason) == ("U", "reflexive_paradigm")
    # TD-08: no route ends in an accept without a human decision.
    assert reflexive_route("high-conf-accept") == "manual_review"
    assert reflexive_route("high-conf-reject") == "manual_review"
    assert reflexive_route("mid-band") == "mid-band"
    assert reflexive_route("manual_review") == "manual_review"


def test_quote_failure_is_u() -> None:
    graded = grade_cell(replace(FULL, quote_ok=False))
    assert (graded.grade, graded.reason) == ("U", "quote_not_verified")


def test_insufficient_calibration_seeds_is_u() -> None:
    graded = grade_cell(replace(FULL, calibration_n=29))
    assert (graded.grade, graded.reason) == ("U", "insufficient_calibration_seeds")


def test_unmeasured_signal_is_u() -> None:
    graded = grade_cell(replace(FULL, error_bound=None))
    assert (graded.grade, graded.reason) == ("U", "signal_not_measured")
    graded2 = grade_cell(replace(FULL, conformal_singleton=None))
    assert graded2.grade == "U"


def test_conformal_multi_set_is_b() -> None:
    graded = grade_cell(replace(FULL, conformal_singleton=False))
    assert (graded.grade, graded.reason) == ("B", "conformal_set_not_singleton")


def test_bound_above_target_is_b() -> None:
    graded = grade_cell(replace(FULL, error_bound=0.06))
    assert (graded.grade, graded.reason) == ("B", "error_bound_above_target")


def test_translation_truth_table() -> None:
    # D-4: untranslated -> no effect (still A).
    assert grade_cell(FULL).grade == "A"
    # not_measured on a translated unit -> U.
    graded = grade_cell(replace(FULL, translation_state="not_measured"))
    assert (graded.grade, graded.reason) == ("U", "signal_not_measured")
    # below floor -> caps at B, reason translation_quality_failed.
    graded = grade_cell(replace(FULL, translation_state="below_floor"))
    assert (graded.grade, graded.reason) == ("B", "translation_quality_failed")
    # ok -> no effect.
    graded = grade_cell(replace(FULL, translation_state="ok"))
    assert graded.grade == "A"


def test_canary_and_contradiction_are_c() -> None:
    graded = grade_cell(replace(FULL, structure_canary_ok=False))
    assert (graded.grade, graded.reason) == ("C", "structure_canary_failed")
    graded = grade_cell(replace(FULL, signals_contradict=True))
    assert (graded.grade, graded.reason) == ("C", "signals_contradict_decision")


def test_reasons_stay_inside_the_closed_list() -> None:
    """Every grade_cell / grade_conformal answer names a CHECK-list reason."""
    variants = [
        FULL,
        replace(FULL, paradigm="reflexive"),
        replace(FULL, quote_ok=False),
        replace(FULL, calibration_n=1),
        replace(FULL, conformal_singleton=False),
        replace(FULL, error_bound=0.9),
        replace(FULL, signals_contradict=True),
        replace(FULL, structure_canary_ok=False),
        replace(FULL, translation_state="not_measured"),
        replace(FULL, translation_state="below_floor"),
    ]
    for variant in variants:
        assert grade_cell(variant).reason in GRADE_REASONS
    for size in (None, 1, 3):
        assert grade_conformal(size).reason in GRADE_REASONS


def test_grade_conformal_seam() -> None:
    assert (grade_conformal(None).grade, grade_conformal(None).reason) == (
        "U",
        "signal_not_measured",
    )
    graded = grade_conformal(3)
    assert (graded.grade, graded.reason) == ("B", "conformal_set_not_singleton")
    graded = grade_conformal(1)
    assert (graded.grade, graded.reason) == ("U", "signal_not_measured")
