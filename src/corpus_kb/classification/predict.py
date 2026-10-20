"""The one scoring orchestration both tiers share (WP-3).

Fit tier routing happens in the handler; from a fitted scorer on, the
path is identical: score every stored chunk, calibrate the scores on the
out-of-fold seed folds, flag low confidence for review, and record every
parameter. Deterministic end to end: stable class order, stable row
order, stable tiebreaks (zero-drift rule 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from . import calibrate, tier_a
from .engine import StoredVector
from .seeds import SeedSet
from .store import PredictionRow

__all__ = ["REVIEW_THRESHOLD", "BuildOutcome", "Scorer", "build_predictions", "seed_texts"]

# Below this calibrated confidence the chunk routes to review (the coding
# review-surface pattern). A code constant, recorded per build in params.
REVIEW_THRESHOLD = 0.6

# Above this class count the build proceeds but records the degradation
# warning (F3, the upstream many-class reports).
MAX_CLASSES_BEFORE_WARNING = 20

MANY_CLASS_WARNING = (
    "many classes: accuracy drops as class count grows. More seeds per "
    "class help; keep classes distinct."
)


class Scorer(Protocol):
    """The fitted-tier surface the orchestration reads (both tiers)."""

    @property
    def classes(self) -> list[str]: ...

    @property
    def params(self) -> dict[str, object]: ...

    def decision_scores(self, features: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class BuildOutcome:
    """The scored corpus: rows ready for storage plus the params record."""

    rows: list[PredictionRow]
    params: dict[str, object]
    classes: list[str]


def seed_texts(seed_set: SeedSet, vectors: list[StoredVector]) -> list[str]:
    """The seed texts in seed-pair order (the tier-B training rows)."""
    text_by_id = {vector.chunk_id: vector.text for vector in vectors}
    return [text_by_id[chunk_id] for chunk_id, _label in seed_set.pairs]


def build_predictions(
    scorer: Scorer,
    seed_features: np.ndarray,
    labels: np.ndarray,
    vectors: list[StoredVector],
    all_features: np.ndarray,
    base_params: dict[str, object],
) -> BuildOutcome:
    """Score, calibrate, and flag the corpus; rows arrive id-sorted.

    ``base_params`` carries the build-level keys (seed file hash, corpus
    counts) and merges over the tier's own record.
    """
    scores = scorer.decision_scores(all_features)
    predicted, raw_probabilities = tier_a.predict_from_scores(scores, scorer.classes)
    params: dict[str, object] = {**scorer.params, **base_params}
    calibrated = _calibrated(scorer, seed_features, labels, scores, raw_probabilities, params)
    confidences = calibrated.max(axis=1)
    reviews = confidences < REVIEW_THRESHOLD
    params["review_threshold"] = REVIEW_THRESHOLD
    _warn_many_classes(scorer, params)
    rows = [
        PredictionRow(
            chunk_id=vector.chunk_id,
            label=predicted[position],
            probability=float(raw_probabilities[position].max()),
            calibrated_probability=float(confidences[position]),
            review=bool(reviews[position]),
        )
        for position, vector in enumerate(vectors)
    ]
    return BuildOutcome(rows=rows, params=params, classes=scorer.classes)


def _calibrated(
    scorer: Scorer,
    seed_features: np.ndarray,
    labels: np.ndarray,
    scores: np.ndarray,
    raw_probabilities: np.ndarray,
    params: dict[str, object],
) -> np.ndarray:
    """Calibrated probabilities, or the raw ones when the folds are too thin."""
    oof, cv, status = calibrate.out_of_fold_scores(seed_features, labels, scorer.classes)
    if oof is None:
        params["calibration"] = status
        return raw_probabilities
    calibration = calibrate.fit_platt(oof, labels, scorer.classes, cv)
    params["calibration"] = calibration.status
    params["calibration_cv"] = cv
    return calibrate.apply_platt(calibration, scores)


def _warn_many_classes(scorer: Scorer, params: dict[str, object]) -> None:
    """Record the many-class warning without blocking the build (F3)."""
    if len(scorer.classes) <= MAX_CLASSES_BEFORE_WARNING:
        return
    recorded = params.get("warnings")
    warnings = list(recorded) if isinstance(recorded, list) else []
    warnings.append(MANY_CLASS_WARNING)
    params["warnings"] = warnings
