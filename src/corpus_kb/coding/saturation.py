"""Saturation calculation: ISR (Incremental Sampling Rate) and stopping rules.

U36 (v6 §7) adds the saturation CURVE: the cumulative unique-to-total code
ratio by unit index, beside (not replacing) the scalar ISR gate above. The
verified reconciliation found no existing cumulative curve emission, so
``saturation_curve`` below is new; ``isr``/``run_stop`` are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


def isr(unique_codes: int, total_applications: int) -> float:
    """Calculate Incremental Sampling Rate (ISR).

    ISR measures the ratio of unique codes to total coded applications.
    Higher ISR (closer to 1.0) indicates more code discovery per unit work.

    Args:
        unique_codes: Count of distinct codes applied so far
        total_applications: Total number of coding decisions made

    Returns:
        ISR value in [0, 1]
    """
    if total_applications == 0:
        return 0.0
    return min(1.0, unique_codes / total_applications)


def run_stop(
    new_codes: int,
    base_unique: int,
    threshold: float = 0.05,
    min_samples: int = 50,
) -> bool:
    """Determine if coding can stop based on saturation rule.

    Args:
        new_codes: Number of new codes discovered in the last run
        base_unique: Total unique codes discovered so far
        threshold: ISR threshold for stopping (default 0.05)
        min_samples: Minimum samples before stopping allowed (default 50)

    Returns:
        True if saturation reached and coding can stop, False otherwise
    """
    if base_unique < min_samples:
        return False

    if base_unique == 0:
        return False

    # Check if new code discovery rate is below threshold
    new_rate = new_codes / base_unique if base_unique > 0 else 0
    return new_rate <= threshold


@dataclass(frozen=True)
class SaturationCurve:
    """Cumulative unique-to-total code ratio by unit index (U36).

    ``unit_index`` is 1-based and aligned with the input order; ``ratio[i]``
    is exactly the scalar ``isr`` evaluated at the prefix ending at that
    unit, so the final entry equals the whole-run ISR.
    """

    unit_index: list[int] = field(default_factory=list)
    cumulative_unique: list[int] = field(default_factory=list)
    total_applications: list[int] = field(default_factory=list)
    ratio: list[float] = field(default_factory=list)


def saturation_curve(unit_code_sets: Sequence[Sequence[str]]) -> SaturationCurve:
    """Cumulative unique codes / total applications by unit index (U36).

    Args:
        unit_code_sets: One sequence of applied code ids per unit, in
            processing order. A unit applying the same code twice counts
            both applications in the total (only the FIRST occurrence joins
            the unique set), matching how the scalar ISR treats repeats.

    Returns:
        SaturationCurve. Empty input -> an empty curve (no fabricated
        points); reports can plot ``unit_index`` vs ``ratio`` directly.
    """
    seen: set[str] = set()
    curve = SaturationCurve()
    total_applications = 0
    for i, codes in enumerate(unit_code_sets, start=1):
        total_applications += len(codes)
        seen.update(codes)
        curve.unit_index.append(i)
        curve.cumulative_unique.append(len(seen))
        curve.total_applications.append(total_applications)
        curve.ratio.append(isr(len(seen), total_applications))
    return curve
