"""Judgment-distribution mean (U35; v6 §7 U35).

Where the local runtime exposes token probabilities, use the MEAN over the
sampled/probabilistic outputs instead of the single greedy (mode) label.
Strictly behind an explicit config key (``judgment_distribution.use_mean``
in ``MEASUREMENT_CONFIG_DEFAULTS``): when the key is off — or the runtime
provided no samples — the mode is reported and ``used_mean`` is False, so
doctor (Wave 2 wiring) can verify the prerequisite honestly.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class JudgmentMeanResult:
    """Judgment distribution summary for one coding decision (U35)."""

    status: str  # ok | insufficient_data
    mean_value: float | None  # distribution mean (None with no samples)
    mode_value: float | None  # modal sample (None with no samples)
    used_mean: bool  # True only when enabled AND samples exist
    n_samples: int


def judgment_mean(sampled_values: Sequence[float], *, enabled: bool) -> JudgmentMeanResult:
    """Mean over the judgment distribution vs the mode, config-gated (U35).

    Args:
        sampled_values: Numeric judgments sampled from the model's output
            distribution (e.g. temperature resamples of a 0-1 score). Empty
            -> ``insufficient_data`` with both values None.
        enabled: The ``judgment_distribution.use_mean`` config value. False
            -> the mode is reported with ``used_mean`` False even when
            samples exist (feature off is an honest off).

    Returns:
        JudgmentMeanResult. ``mean_value`` is the arithmetic mean of the
        samples; ``mode_value`` the most frequent (ties -> lowest value,
        deterministic via Counter.most_common insertion order on sorted
        input).
    """
    if len(sampled_values) == 0:
        return JudgmentMeanResult(
            status="insufficient_data",
            mean_value=None,
            mode_value=None,
            used_mean=False,
            n_samples=0,
        )
    arr = np.asarray(sampled_values, dtype=float)
    counts = Counter(arr.tolist())
    # Highest count, ties to the lowest value (deterministic).
    mode = min(counts, key=lambda v: (-counts[v], v))
    used_mean = enabled
    return JudgmentMeanResult(
        status="ok",
        mean_value=float(np.mean(arr)) if enabled else float(mode),
        mode_value=float(mode),
        used_mean=used_mean,
        n_samples=int(arr.size),
    )
