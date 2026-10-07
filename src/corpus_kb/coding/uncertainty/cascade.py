"""Tier-3 cascade orchestration: escalation selection + combined routing.

The cascade runs tiers 0-2 (free) on every unit, escalates the flagged
MINORITY (10-20% of the corpus — Tier 3 costs ~5x via N=5 resamples + 10 NLI
calls per escalated unit, r11), computes the combined review priority, and
routes via the top-2-margin ambiguity rule. Everything is injectable:
``resample_fn`` produces the N rationale strings for one unit (live: same
fixed prompt, varied temperature/seed only; fixtures: recorded rationales)
and ``nli_fn`` is the mutual-entailment judge (live: fixed-prompt
temperature-0 Qwen3 call; tests: recorded offline fixtures).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from corpus_kb.coding.uncertainty.review_priority import (
    REVIEW_REASONS,
    ReviewSignal,
    priority_scores,
    route_reasons,
    top2_margins,
)
from corpus_kb.coding.uncertainty.tier3_consistency import (
    N_SAMPLES,
    self_consistency,
)

ESCALATION_FRACTION = 0.10
ESCALATION_BAND = (0.10, 0.20)


@dataclass(frozen=True)
class RoutedUnit:
    """One unit's cascade outcome."""

    unit_index: int
    priority: float
    reason: str
    escalated: bool
    semantic_entropy: float | None = None
    margin: float = 0.0


@dataclass(frozen=True)
class CascadeResult:
    """Whole-corpus cascade outcome (fixture-audit shape)."""

    routed: list[RoutedUnit]
    escalation_proportion: float
    n_escalated: int
    n_units: int
    n_nli_calls: int
    reasons: list[str] = field(default_factory=list)


def select_escalation(preliminary_priority: np.ndarray | Sequence[float]) -> list[int]:
    """Indices of the flagged minority: top ESCALATION_FRACTION of units.

    ``k = ceil(0.10 * n)`` keeps the flagged share at or above the 10% band
    floor at every corpus size (floor() would undershoot, e.g. 8/82); at
    least one unit is escalated so a tiny corpus still exercises the tier-3
    path (proportions above the band on corpora of <10 units are a documented
    small-corpus caveat, asserted in-band at realistic sizes by the fixture
    acceptance tests).
    """
    n = len(preliminary_priority)
    if n == 0:
        return []
    k = min(n, max(1, int(np.ceil(ESCALATION_FRACTION * n))))
    order = sorted(range(n), key=lambda i: (-float(preliminary_priority[i]), i))
    return order[:k]


def escalation_proportion(n_escalated: int, n_units: int) -> float:
    """Flagged-minority share (0.0 when the corpus is empty)."""
    if n_units == 0:
        return 0.0
    return n_escalated / n_units


def run_cascade(
    code_scores: np.ndarray,
    base_signals: Sequence[ReviewSignal],
    resample_fn: Callable[[int], Sequence[str]],
    nli_fn: Callable[[str, str], bool],
    decision_reasons: Sequence[str],
    delta_amb: float,
    weights: dict[str, float] | None = None,
    n_samples: int = N_SAMPLES,
) -> CascadeResult:
    """Run the full cascade over one corpus.

    Args:
        code_scores: (n_units, n_codes) score matrix for margins/routing.
        base_signals: Tiers 0-2 + link/qdep signals per unit (SE unset).
        resample_fn: unit_index -> N resampled rationale strings (same fixed
            prompt, varied temperature/seed only).
        nli_fn: Bidirectional mutual-entailment judge for a rationale pair.
        decision_reasons: Raw deductive decision reason per unit (normalized
            onto the closed enum at the routing boundary).
        delta_amb: Calibrated top-2-margin ambiguity threshold.
        weights: Optional refit weights; equal weights by default.
        n_samples: Resample count (default 5 -> 10 NLI calls per escalated unit).

    Returns:
        CascadeResult with per-unit priority + closed-enum reasons, the
        escalation proportion, and the total NLI call count.
    """
    if len(base_signals) != code_scores.shape[0] or len(decision_reasons) != len(base_signals):
        raise ValueError("code_scores, base_signals, decision_reasons must align")
    preliminary = priority_scores(base_signals, weights)
    escalated_idx = set(select_escalation(preliminary))

    margins = top2_margins(code_scores)
    signals = list(base_signals)
    nli_calls = 0
    se_by_unit: dict[int, float] = {}
    for idx in sorted(escalated_idx):
        rationales = list(resample_fn(idx))
        result = self_consistency(rationales, nli_fn)
        nli_calls += result.n_nli_calls
        se_by_unit[idx] = result.semantic_entropy
        signals[idx] = ReviewSignal(
            margin=signals[idx].margin,
            h_tok=signals[idx].h_tok,
            se=result.semantic_entropy,
            hedge=signals[idx].hedge,
            interpretive=signals[idx].interpretive,
            link_score=signals[idx].link_score,
            question_dependent=signals[idx].question_dependent,
        )

    final = priority_scores(signals, weights)
    reasons = route_reasons(margins, delta_amb, decision_reasons)
    routed = [
        RoutedUnit(
            unit_index=i,
            priority=float(final[i]),
            reason=reasons[i],
            escalated=i in escalated_idx,
            semantic_entropy=se_by_unit.get(i),
            margin=float(margins[i]),
        )
        for i in range(len(signals))
    ]
    n = len(signals)
    return CascadeResult(
        routed=routed,
        escalation_proportion=escalation_proportion(len(escalated_idx), n),
        n_escalated=len(escalated_idx),
        n_units=n,
        n_nli_calls=nli_calls,
        reasons=reasons,
    )


def assert_reasons_closed(reasons: Sequence[str]) -> None:
    """Hard assert that every routing reason is in the CLOSED enum (r11)."""
    for reason in reasons:
        if reason not in REVIEW_REASONS:
            raise ValueError(f"reason {reason!r} outside the closed review enum")
