from __future__ import annotations

import math

import numpy as np

from corpus_kb.coding.uncertainty.tier0_similarity import (
    kmedoids_prototypes,
    max_prototype_sim,
)
from corpus_kb.coding.uncertainty.tier1_logprob import logprob_entropy
from corpus_kb.coding.uncertainty.tier2_hedging import hedging_score


def test_tiers() -> None:
    protos = kmedoids_prototypes([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], k=2)
    assert len(protos) == 2
    assert max_prototype_sim([1.0, 0.0], protos) > 0.9
    # near-uniform top logprobs -> high entropy
    lp = [{"top_logprobs": [{"logprob": math.log(0.5)}, {"logprob": math.log(0.5)}]}]
    assert logprob_entropy(lp) > 0.6
    assert hedging_score("it possibly seems like maybe this applies") >= 3
    assert hedging_score("this is the control being tested") == 0


def _cascade_corpus() -> tuple[np.ndarray, list, list[str]]:
    """20 units: 19 clear (wide margin) + 1 ambiguous (thin margin)."""
    from corpus_kb.coding.uncertainty.review_priority import ReviewSignal

    scores = np.array([[0.90, 0.20, 0.10]] * 19 + [[0.55, 0.52, 0.10]], dtype=np.float32)
    signals = [ReviewSignal(margin=0.70)] * 19 + [ReviewSignal(margin=0.03)]
    return scores, signals, ["explicit"] * 20


def test_run_cascade_empty_resamples_leave_se_unmeasured() -> None:
    """A degraded escalation fabricates nothing: SE stays unset, NLI calls 0."""
    import numpy as np

    from corpus_kb.coding.uncertainty.cascade import run_cascade

    scores, signals, reasons = _cascade_corpus()
    result = run_cascade(
        scores, signals, lambda i: [], lambda a, b: False, reasons, 0.05, escalate=True
    )
    escalated = [r for r in result.routed if r.escalated]
    assert escalated, "the flagged minority still escalates"
    assert all(r.semantic_entropy is None for r in escalated)
    assert result.n_nli_calls == 0
    assert isinstance(result.escalation_proportion, float)
    assert result.escalation_proportion == len(escalated) / float(len(result.routed))
    assert np.isfinite(result.routed[-1].priority)


def test_run_cascade_escalate_false_never_calls_resamples() -> None:
    """Disabled tier-3 marks no unit escalated and pays no model calls."""
    from corpus_kb.coding.uncertainty.cascade import run_cascade

    scores, signals, reasons = _cascade_corpus()

    def forbidden(idx: int) -> list[str]:
        raise AssertionError("resample_fn must not be called when escalate is False")

    result = run_cascade(
        scores, signals, forbidden, lambda a, b: False, reasons, 0.05, escalate=False
    )
    assert result.n_escalated == 0
    assert result.n_nli_calls == 0
    assert result.escalation_proportion == 0.0
    assert all(r.escalated is False for r in result.routed)
    assert all(r.semantic_entropy is None for r in result.routed)
    # routed stays in unit order; the ambiguous unit must rank highest.
    priorities = [r.priority for r in result.routed]
    assert max(priorities) == result.routed[19].priority
