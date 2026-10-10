"""U41/U47/U37 routing composition for the review surface (v8 §3).

Chains the wave-1 modules into one routing decision per unit:

  1. U47 ``confidence_calibration.choose_routing_signal`` picks the routing
     score: best calibrated signal on the CALIBRATION split, evaluated ONCE
     on the test split. Outcomes are oriented 1 = CORRECT there.
  2. U41 ``risk_coverage.threshold_for_target_risk`` derives the auto-accept
     threshold from ``routing.target_risk`` under the exact Clopper-Pearson
     bound (its outcome orientation is 1 = ERROR, converted here — never
     mixed). Calibrated scores feed both splits.
  3. Units at/above the threshold are AUTO-ACCEPTED and a seeded audit
     sample (``audit.sample_rate`` + ``audit.seed``, U17 design) is drawn
     from them; units below route to the gray-zone review queue with the
     reason recorded. ``insufficient_data`` — too few calibration labels,
     a single-class split, or no threshold inside the CP bound — routes
     EVERYTHING to review with that reason.

  4. U37 ``review_batch.select_review_batch`` selects the next human batch
     from whatever is routed to review (uncertainty + diversity + the
     always-present random stratum), with batch_size/random_fraction/
     diversity read from config.

All thresholds come from ``coding/measurement_settings.py`` typed settings
(``measurement_settings_from_mapping`` over the ``research.*`` block) — no
silent defaults at the call sites.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np

from corpus_kb.coding.confidence_calibration import choose_routing_signal
from corpus_kb.coding.measurement_settings import (
    AuditSettings,
    MeasurementSettings,
    ReviewSettings,
    RoutingSettings,
)
from corpus_kb.coding.reliability_sampling import AuditSampleRow, audit_partitions
from corpus_kb.coding.risk_coverage import INSUFFICIENT_DATA, OK, threshold_for_target_risk
from corpus_kb.research.review_batch import ReviewBatch, select_review_batch

ROUTE_AUTO_ACCEPT = "auto_accept"
ROUTE_REVIEW = "review"
REASON_INSUFFICIENT = INSUFFICIENT_DATA
REASON_BELOW_THRESHOLD = "below_routing_threshold"
REASON_AT_THRESHOLD = "at_or_above_threshold"
AUDIT_SCOPE = "routing_auto_accept"


@dataclass(frozen=True)
class RoutingDecision:
    """One unit's route with its calibrated score and the reason."""

    unit_id: str
    route: str  # auto_accept | review
    score: float  # calibrated probability of correctness
    reason: str


@dataclass(frozen=True)
class RoutingPlan:
    """The routing surface for one deductive run's units (U41/U47)."""

    status: str  # ok | insufficient_data
    chosen_signal: str
    calibrator_kind: str
    threshold: float | None
    target_risk: float
    decisions: tuple[RoutingDecision, ...]
    audit_sample: tuple[AuditSampleRow, ...]
    n_auto_accept: int
    n_review: int
    reason: str


def _review_everything(
    unit_scores: Mapping[str, float], reason: str
) -> tuple[RoutingDecision, ...]:
    return tuple(
        RoutingDecision(unit_id=u, route=ROUTE_REVIEW, score=0.0, reason=reason)
        for u in unit_scores
    )


def route_for_review(
    calibration_signals: Mapping[str, Sequence[float]],
    calibration_correct: Sequence[float],
    test_signals: Mapping[str, Sequence[float]],
    test_correct: Sequence[float],
    unit_scores: Mapping[str, float],
    unit_codes: Mapping[str, str],
    settings: MeasurementSettings,
) -> RoutingPlan:
    """Route every unit: auto-accept + audit sample, or review (U41/U47).

    Args:
        calibration_signals: Candidate signal values on the calibration
            split (higher = more likely correct, U47 orientation).
        calibration_correct: 1.0 where the calibration label was CORRECT.
        test_signals / test_correct: The held-out test split (evaluated ONCE).
        unit_scores: Production units' values of the SAME signals; the
            chosen calibrator maps them to routing scores.
        unit_codes: Unit -> code id (audit strata key).
        settings: Typed measurement settings (routing.*, audit.*).

    Returns:
        RoutingPlan. On ``insufficient_data`` every decision routes to
        review with that reason and the audit sample is empty.
    """
    routing = cast(RoutingSettings, settings.routing)
    audit = cast(AuditSettings, settings.audit)
    choice = choose_routing_signal(
        dict(calibration_signals),
        list(calibration_correct),
        dict(test_signals),
        list(test_correct),
    )
    if choice.status != OK:
        return _insufficient_plan(unit_scores, routing, choice.chosen_signal, choice.reason)
    calibrator = choice.calibrator
    chosen = choice.chosen_signal
    scores_cal = calibrator.predict(np.asarray(list(calibration_signals[chosen]), dtype=float))
    scores_test = calibrator.predict(np.asarray(list(test_signals[chosen]), dtype=float))
    errors_cal = 1.0 - np.asarray(list(calibration_correct), dtype=float)
    errors_test = 1.0 - np.asarray(list(test_correct), dtype=float)
    result = threshold_for_target_risk(
        scores_cal,
        errors_cal,
        scores_test,
        errors_test,
        target_risk=routing.target_risk,
        confidence=routing.confidence_level,
        min_calibration_n=routing.min_calibration_n,
    )
    if result.status != OK or result.threshold is None:
        return _insufficient_plan(unit_scores, routing, choice.chosen_signal, result.reason)
    threshold = float(result.threshold)
    decisions: list[RoutingDecision] = []
    accepted: list[tuple[str, str, float]] = []
    for unit_id, raw in unit_scores.items():
        score = float(calibrator.predict(np.asarray([float(raw)], dtype=float))[0])
        if score >= threshold:
            decisions.append(
                RoutingDecision(unit_id, ROUTE_AUTO_ACCEPT, score, REASON_AT_THRESHOLD)
            )
            accepted.append((unit_id, unit_codes.get(unit_id, "unknown"), score))
        else:
            decisions.append(RoutingDecision(unit_id, ROUTE_REVIEW, score, REASON_BELOW_THRESHOLD))
    sample: list[AuditSampleRow] = []
    if accepted:
        target_n = round(len(accepted) * audit.sample_rate)
        if target_n > 0:
            sample = audit_partitions(
                [{"unit_id": u, "code_id": c, "agreement": s} for u, c, s in accepted],
                target_n,
                audit.seed,
                scope=AUDIT_SCOPE,
            )
    return RoutingPlan(
        status=OK,
        chosen_signal=choice.chosen_signal,
        calibrator_kind=calibrator.kind,
        threshold=threshold,
        target_risk=routing.target_risk,
        decisions=tuple(decisions),
        audit_sample=tuple(sample),
        n_auto_accept=len(accepted),
        n_review=len(decisions) - len(accepted),
        reason=result.reason,
    )


def _insufficient_plan(
    unit_scores: Mapping[str, float],
    routing: RoutingSettings,
    chosen_signal: str,
    reason: str,
) -> RoutingPlan:
    """Honest fail-safe: everything routes to review (U41 contract)."""
    decisions = _review_everything(unit_scores, REASON_INSUFFICIENT)
    return RoutingPlan(
        status=INSUFFICIENT_DATA,
        chosen_signal=chosen_signal,
        calibrator_kind="identity",
        threshold=None,
        target_risk=routing.target_risk,
        decisions=decisions,
        audit_sample=(),
        n_auto_accept=0,
        n_review=len(decisions),
        reason=reason,
    )


def next_review_batch(
    unit_ids: Sequence[str],
    confidences: Mapping[str, float],
    embeddings: Mapping[str, Sequence[float]] | None,
    reviewed: Sequence[str],
    review: ReviewSettings,
    seed: int = 0,
) -> ReviewBatch:
    """U37 next human-review batch (config-read sizes, review_batch math)."""
    return select_review_batch(
        unit_ids,
        confidences,
        embeddings,
        reviewed=reviewed,
        batch_size=review.batch_size,
        random_fraction=review.random_fraction,
        diversity=review.diversity,
        seed=seed,
    )


def measurement_settings_from_config(config: Mapping[str, object]) -> MeasurementSettings:
    """The typed reader over the loaded config's ``research`` block.

    ``measurement_settings_from_mapping`` is THE reader for the measurement
    blocks; the loaded config nests them under ``research``, so this is the
    one adapter the cycle/review paths call (fail-loud on wrong types).
    """
    from corpus_kb.coding.measurement_settings import measurement_settings_from_mapping

    research = config.get("research")
    if research is not None and not isinstance(research, dict):
        raise ValueError("config.research must be a mapping")
    return measurement_settings_from_mapping(cast("dict[str, object]", research or {}))
