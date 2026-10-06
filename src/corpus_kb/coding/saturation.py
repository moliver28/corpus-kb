"""Saturation calculation: ISR (Incremental Sampling Rate) and stopping rules."""

from __future__ import annotations


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
