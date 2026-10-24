"""Belief eras via Beta-Bernoulli BOCPD (Living Corpus W4).

Bayesian Online Change-Point Detection (Adams & MacKay 2007) over the
belief's weighted stance stream: the run-length posterior segments the
history at stance shifts, so "likes ice cream … likes … got tired of it"
reads as two eras with the shift dated — the historical relationship is
kept, not overwritten. Deterministic (max over run probabilities, fixed
hazard); O(n) per belief.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .math_core import Evidence

HAZARD = 1.0 / 100.0  # prior expected run length: 100 evidence items


@dataclass(frozen=True)
class Era:
    """One maximal run of consistent stance."""

    start: datetime
    end: datetime
    positives: int
    negatives: int
    mean: float


def eras(
    evidence: list[Evidence],
    *,
    shift_threshold: float = 0.35,
) -> list[Era]:
    """Segment the evidence stream at detected stance shifts.

    The BOCPD run posterior with a Beta-Bernoulli learner picks the MAP
    split points; a split survives when the segment means around it move
    at least ``shift_threshold``. With no split, the whole stream is one
    era.
    """
    if not evidence:
        return []
    ordered = sorted(evidence, key=lambda e: e.observed_at)
    splits = _map_splits([1.0 if e.stance == "positive" else 0.0 for e in ordered])
    bounds = [0, *splits, len(ordered)]
    out: list[Era] = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        segment = ordered[start:end]
        positives = sum(1 for e in segment if e.stance == "positive")
        negatives = len(segment) - positives
        out.append(
            Era(
                start=segment[0].observed_at,
                end=segment[-1].observed_at,
                positives=positives,
                negatives=negatives,
                mean=round(positives / len(segment), 6),
            )
        )
    return _drop_small_moves(out, shift_threshold)


def _map_splits(values: list[float]) -> list[int]:
    """BOCPD run-length MAP over a Bernoulli stream; returns split indexes."""
    if len(values) < 4:
        return []
    log_runs = [0.0]
    alpha, beta = 1.0, 1.0
    splits: list[int] = []
    for t, x in enumerate(values[1:], start=1):
        predictive_pos = alpha / (alpha + beta)
        likelihood = predictive_pos if x >= 0.5 else (1.0 - predictive_pos)
        growth = [lr + math.log(likelihood) for lr in log_runs]
        change_prob = math.log(HAZARD) + max(growth) if growth else math.log(HAZARD)
        log_runs = [lr + math.log(likelihood) + math.log(1.0 - HAZARD) for lr in growth] + [
            change_prob
        ]
        if x >= 0.5:
            alpha += 1.0
        else:
            beta += 1.0
        run_map = max(range(len(log_runs)), key=lambda i: log_runs[i])
        if run_map == 0 and t >= 2:
            splits.append(t)
    return splits


def _drop_small_moves(segments: list[Era], threshold: float) -> list[Era]:
    """Merge adjacent eras whose means barely move (noise, not change)."""
    if len(segments) < 2:
        return segments
    out = [segments[0]]
    for segment in segments[1:]:
        if abs(segment.mean - out[-1].mean) < threshold:
            merged_pos = out[-1].positives + segment.positives
            merged_neg = out[-1].negatives + segment.negatives
            total = merged_pos + merged_neg
            out[-1] = Era(
                start=out[-1].start,
                end=segment.end,
                positives=merged_pos,
                negatives=merged_neg,
                mean=round(merged_pos / total, 6) if total else 0.0,
            )
        else:
            out.append(segment)
    return out
