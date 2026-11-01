"""Pure helpers for the labeling triage handler (the 250-line move).

The handler module (:mod:`labeling_triage_tools`) keeps the async seams
and the one shared operation; the param validation, the envelope
builders, and the fold-coverage math live here so both files stay under
the size cap (the ``handlers/coding_ops.py`` precedent). Nothing here
touches the database: every function is pure and deterministic.
"""

from __future__ import annotations

from ..coding.label_triage import LabelReviewItem
from ..errors import ValidationError

__all__ = [
    "DEFAULT_FLAG_THRESHOLD",
    "DEFAULT_QUEUE_LIMIT",
    "DEFAULT_TRIAGE_FOLDS",
    "MAX_QUEUE_LIMIT",
    "honest_envelope",
    "label_map",
    "queue_row",
    "thinnest_class",
    "validated_folds",
    "validated_limit",
    "validated_threshold",
]

# Op defaults and bounds (the labeling_tools conventions).
DEFAULT_TRIAGE_FOLDS = 5
DEFAULT_FLAG_THRESHOLD = 0.2
DEFAULT_QUEUE_LIMIT = 50
MAX_QUEUE_LIMIT = 500

_TRIAGE_SEED = 0  # pinned: one build, one queue (deterministic output rule)

_UNAVAILABLE_REASON = "labeling tables absent (migration 028 not applied)"
_UNAVAILABLE_REMEDY = "run corpus-kb setup"


def queue_row(item: LabelReviewItem, ids: list[str], classes: list[str]) -> dict[str, object]:
    """One queue row: indices resolved to chunk ids and class names."""
    return {
        "chunk_id": ids[item.unit_index],
        "assigned_label": classes[item.assigned_label],
        "oos_probability": item.oos_probability,
        "runner_up_label": classes[item.runner_up_label],
        "runner_up_probability": item.runner_up_probability,
    }


def thinnest_class(classes: list[str], labels: list[int], folds: int) -> tuple[str, int] | None:
    """(name, count) when a class sits below the fold floor, else None.

    A class with fewer members than ``cv_folds`` can leave one fold's
    train split without it, and the module's per-fold shape check would
    raise a raw error. The guard refuses that state up front.
    """
    counts = [0] * len(classes)
    for label in labels:
        counts[label] += 1
    name, count = min(zip(classes, counts, strict=True), key=lambda item: item[1])
    if count < folds:
        return name, count
    return None


def honest_envelope(
    status: str,
    reason: str,
    remedy: str,
    folds: int,
    threshold: float,
    n_units: int = 0,
    classes: list[str] | None = None,
) -> dict[str, object]:
    """The guard envelope on the triage keys (reason + remedy named)."""
    return {
        "status": status,
        "n_flagged": 0,
        "queue": [],
        "n_units": n_units,
        "cv_folds": folds,
        "flag_threshold": threshold,
        "classes": classes or [],
        "reason": reason,
        "remedy": remedy,
    }


def label_map(rows: list[dict[str, object]]) -> dict[str, str]:
    """chunk_id -> label; the first row wins if the store ever repeats."""
    label_map: dict[str, str] = {}
    for row in rows:
        chunk_id = str(row["chunk_id"])
        if chunk_id not in label_map:
            label_map[chunk_id] = str(row["label"])
    return label_map


def validated_folds(cv_folds: int) -> int:
    """One fold would see its own rows; two is the honest floor."""
    if isinstance(cv_folds, bool) or not isinstance(cv_folds, int) or cv_folds < 2:
        raise ValidationError("cv_folds must be a whole number of 2 or more.")
    return int(cv_folds)


def validated_threshold(flag_threshold: float) -> float:
    """The flag threshold is a probability: inside (0, 1]."""
    if (
        isinstance(flag_threshold, bool)
        or not isinstance(flag_threshold, (int, float))
        or not 0.0 < float(flag_threshold) <= 1.0
    ):
        raise ValidationError("flag_threshold must be a number greater than 0 and at most 1.")
    return float(flag_threshold)


def validated_limit(limit: int) -> int:
    """The queue head bound (the labeling read bounds)."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit must be a whole number of 1 or more.")
    return min(int(limit), MAX_QUEUE_LIMIT)
