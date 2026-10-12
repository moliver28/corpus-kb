"""Per-unit cross-run agreement score (U18 support; v6 §7 U18/U19).

The PRIMARY uncertainty signal for review routing is the agreement across
repeated coding runs of the machine coder — NOT verbalized confidence. This
module computes that per-unit score from K run label vectors (pairwise
agreement over runs, ties included). The score is ORDINAL input for the
U47 calibration harness (AUROC/ECE/Brier against human-validated
correctness) before it may gate anything; it is not itself a probability.

Pure numpy; deterministic; no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CrossRunAgreement:
    """Per-unit cross-run agreement (higher = more stable runs)."""

    score: float | None  # fraction of agreeing run pairs; None when K < 2
    n_runs: int
    n_pairs: int  # C(K, 2) pairs compared
    n_agreeing: int


def cross_run_agreement(labels: Sequence[object]) -> CrossRunAgreement:
    """Agreement across the K run labels of ONE unit.

    Args:
        labels: The label this unit received in each of K coding runs
            (any hashable label type; None/missing entries count as
            disagreements with everything, including themselves — a run
            that did not produce a label cannot agree).

    Returns:
        CrossRunAgreement with the fraction of agreeing pairs. Fewer than
        2 runs -> score None (honest not-measured, never 1.0).
    """
    k = len(labels)
    n_pairs = k * (k - 1) // 2
    if k < 2:
        return CrossRunAgreement(score=None, n_runs=k, n_pairs=0, n_agreeing=0)
    n_agreeing = 0
    for i in range(k):
        for j in range(i + 1, k):
            li, lj = labels[i], labels[j]
            if li is not None and lj is not None and li == lj:
                n_agreeing += 1
    return CrossRunAgreement(
        score=n_agreeing / n_pairs,
        n_runs=k,
        n_pairs=n_pairs,
        n_agreeing=n_agreeing,
    )


def cross_run_agreement_batch(run_labels: Sequence[Sequence[object]]) -> np.ndarray:
    """Per-unit agreement scores for n_units x K run labels.

    Units with fewer than 2 run labels score NaN (not measured) — the
    calibration harness treats NaN as unmeasured, never as 0 or 1.
    """
    return np.asarray(
        [cross_run_agreement(unit).score if len(unit) >= 2 else np.nan for unit in run_labels],
        dtype=float,
    )
