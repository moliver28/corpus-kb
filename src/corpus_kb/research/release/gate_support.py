"""Shared coercion + confusion-math helpers for the wave-2 release gates.

The gate functions are pure over ``GateInputs`` whose evidence blocks are
JSON-shaped (``dict[str, object]``); these helpers turn the loose values
into typed ones WITHOUT ever inventing data: a missing or malformed field
yields ``None``/``[]`` and the caller answers ``not_evaluable``/``fail``
honestly. Kept in one module so every gate file stays under the 250-line
soft limit.
"""

from __future__ import annotations


def as_dict(value: object) -> dict[str, object]:
    """A real mapping view, or {} for anything else (no fabrication)."""
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[object]:
    """A real list view, or [] for anything else."""
    return value if isinstance(value, list) else []


def as_str(value: object) -> str | None:
    """Non-empty str view; None for missing/empty/non-string."""
    return value if isinstance(value, str) and value.strip() else None


def as_int(value: object) -> int | None:
    """Int view (bools rejected); None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def as_float(value: object) -> float | None:
    """Float view (bools rejected); None for missing/non-numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def confusion_prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    """Precision/recall/F1 from confusion counts (None when undefined).

    A code with no predicted positives has undefined precision; no positives
    at all has undefined recall; F1 needs both. Callers report the None case
    as honestly not-measured rather than a fabricated 0.0.
    """
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is None or recall is None or (precision + recall) == 0:
        f1: float | None = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def stratum_confusion(raw: dict[str, object]) -> tuple[int, int, int, int] | None:
    """(tp, fp, fn, n) out of one stratum/count block, or None when unusable."""
    tp = as_int(raw.get("tp"))
    fp = as_int(raw.get("fp"))
    fn = as_int(raw.get("fn"))
    if tp is None or fp is None or fn is None:
        return None
    n_raw = raw.get("n")
    n = as_int(n_raw) if n_raw is not None else tp + fp + fn
    if n is None or n < 0:
        n = tp + fp + fn
    return tp, fp, fn, n
