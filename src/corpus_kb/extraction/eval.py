"""Span-level NER evaluation harness.

Scores predicted mention spans against hand-labeled gold JSONL (see
``_eval_gold.py`` for the file format): per-type plus micro P/R/F1, a
paired bootstrap CI on the F1 delta between two systems, and the
promotion gate: micro F1 >= 0.80 (absolute floor) AND >= baseline
+ 0.02 (relative margin). Matching is exact span identity:
(doc_id, start, end, label).
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ._eval_gold import GoldDoc, GoldSpan, SpanKey, load_gold

__all__ = [
    "ABSOLUTE_FLOOR",
    "RELATIVE_MARGIN",
    "BootstrapResult",
    "EvalReport",
    "GoldDoc",
    "GoldSpan",
    "PromotionDecision",
    "Scored",
    "load_gold",
    "paired_bootstrap",
    "promotion_gate",
    "score_corpus",
]

#: Promotion gate constants (plan-pinned; do not tune per run).
ABSOLUTE_FLOOR = 0.80
RELATIVE_MARGIN = 0.02

#: Float-noise tolerance: 0.80 + 0.02 evaluates above 0.82 in binary floats.
_GATE_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class Scored:
    """Precision/recall/F1 for one span set."""

    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Per-type plus micro scores for one system over one gold set."""

    micro: Scored
    per_type: dict[str, Scored]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Paired bootstrap CI over the document-resampled F1 delta."""

    delta_f1: float
    ci_low: float
    ci_high: float
    resamples: int
    seed: int


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Gate outcome with the exact reasons it passed or failed."""

    promoted: bool
    reasons: tuple[str, ...]


def _score(gold_keys: set[SpanKey], pred_keys: set[SpanKey]) -> Scored:
    """Score one span-key set pair."""
    tp = len(gold_keys & pred_keys)
    fp = len(pred_keys - gold_keys)
    fn = len(gold_keys - pred_keys)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return Scored(tp=tp, fp=fp, fn=fn, precision=precision, recall=recall, f1=f1)


def _keys(gold: Iterable[GoldDoc], predictions: Mapping[str, Iterable[GoldSpan]]) -> set[SpanKey]:
    """Build (doc_id, start, end, label) keys; gold scope only."""
    return {
        (doc.doc_id, span.start, span.end, span.label)
        for doc in gold
        for span in predictions.get(doc.doc_id, ())
    }


def score_corpus(gold: list[GoldDoc], predictions: Mapping[str, Iterable[GoldSpan]]) -> EvalReport:
    """Span-level scores for one system: per type plus micro."""
    gold_by_type: dict[str, set[SpanKey]] = {}
    pred_by_type: dict[str, set[SpanKey]] = {}
    for doc in gold:
        for span in doc.spans:
            gold_by_type.setdefault(span.label, set()).add(
                (doc.doc_id, span.start, span.end, span.label)
            )
        for span in predictions.get(doc.doc_id, ()):
            pred_by_type.setdefault(span.label, set()).add(
                (doc.doc_id, span.start, span.end, span.label)
            )
    labels = sorted(set(gold_by_type) | set(pred_by_type))
    per_type = {
        label: _score(gold_by_type.get(label, set()), pred_by_type.get(label, set()))
        for label in labels
    }
    micro = _score(
        set().union(*gold_by_type.values()) if gold_by_type else set(),
        set().union(*pred_by_type.values()) if pred_by_type else set(),
    )
    return EvalReport(micro=micro, per_type=per_type)


def paired_bootstrap(
    gold: list[GoldDoc],
    candidate: Mapping[str, Iterable[GoldSpan]],
    baseline: Mapping[str, Iterable[GoldSpan]],
    resamples: int = 10_000,
    seed: int = 42,
    confidence: float = 0.95,
) -> BootstrapResult:
    """Paired bootstrap CI on the candidate-minus-baseline micro F1 delta.

    Documents are resampled with replacement (the document is the pairing
    unit); F1 is recomputed for both systems on each resample. Deterministic
    for a fixed (seed, gold, inputs).
    """
    candidate_keys = _keys(gold, candidate)
    baseline_keys = _keys(gold, baseline)
    doc_gold = {
        doc.doc_id: {(doc.doc_id, s.start, s.end, s.label) for s in doc.spans} for doc in gold
    }
    doc_cand: dict[str, set[SpanKey]] = {doc_id: set() for doc_id in doc_gold}
    doc_base: dict[str, set[SpanKey]] = {doc_id: set() for doc_id in doc_gold}
    for key in candidate_keys:
        doc_cand[key[0]].add(key)
    for key in baseline_keys:
        doc_base[key[0]].add(key)

    rng = random.Random(seed)
    doc_ids = list(doc_gold)
    deltas: list[float] = []
    for _ in range(resamples):
        g: set[SpanKey] = set()
        c: set[SpanKey] = set()
        b: set[SpanKey] = set()
        for doc_id in rng.choices(doc_ids, k=len(doc_ids)):
            g |= doc_gold[doc_id]
            c |= doc_cand[doc_id]
            b |= doc_base[doc_id]
        deltas.append(_score(g, c).f1 - _score(g, b).f1)

    deltas.sort()
    alpha = 1.0 - confidence
    low_index = max(0, int((alpha / 2) * resamples))
    high_index = min(resamples - 1, int((1 - alpha / 2) * resamples) - 1)
    all_gold: set[SpanKey] = set().union(*doc_gold.values()) if doc_gold else set()
    point = _score(all_gold, candidate_keys).f1 - _score(all_gold, baseline_keys).f1
    return BootstrapResult(
        delta_f1=point,
        ci_low=deltas[low_index],
        ci_high=deltas[high_index],
        resamples=resamples,
        seed=seed,
    )


def promotion_gate(
    candidate_f1: float,
    baseline_f1: float,
    absolute_floor: float = ABSOLUTE_FLOOR,
    relative_margin: float = RELATIVE_MARGIN,
) -> PromotionDecision:
    """Apply the plan-pinned promotion gate.

    Promote when the candidate's micro F1 clears the absolute floor AND
    beats the baseline by at least the relative margin (both comparisons
    tolerant of float representation noise at the exact boundary).
    """
    floor_ok = candidate_f1 >= absolute_floor
    margin_ok = candidate_f1 >= baseline_f1 + relative_margin - _GATE_EPSILON
    numbers = (
        f"micro F1 {candidate_f1:.4f}, floor {absolute_floor:.2f}, "
        f"baseline {baseline_f1:.4f}, margin {relative_margin:.2f}"
    )
    return PromotionDecision(
        promoted=floor_ok and margin_ok,
        reasons=(
            f"{'PASS' if floor_ok else 'FAIL'} absolute: {numbers}",
            f"{'PASS' if margin_ok else 'FAIL'} relative: {numbers}",
        ),
    )
