"""Fixture acceptance run for the tier-3 cascade (todo 16, r11 contract).

Offline end-to-end: hand-built ambiguous + clear corpora, recorded qwen3 NLI
fixtures. Asserts the acceptance criteria:
  * flagged-minority escalation only (10-20% band)
  * N=5 => exactly 10 pairwise NLI calls per escalated unit
  * hand-built ambiguous cases rank ABOVE clear cases under the priority
  * routing reasons all inside the CLOSED reason enum
  * semantic entropy separates the ambiguous unit (2 clusters) from the
    clear unit (1 cluster)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from corpus_kb.coding.uncertainty.cascade import (
    ESCALATION_BAND,
    run_cascade,
    select_escalation,
)
from corpus_kb.coding.uncertainty.review_priority import REVIEW_REASONS, ReviewSignal

_FIXTURE = Path(__file__).with_name("fixtures") / "research" / "nli_recorded_qwen3.json"


def _fixture_units() -> dict[str, dict]:
    doc = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return {u["unit"]: u for u in doc["units"]}


def _judge_for(units: dict[str, dict], name: str):
    from corpus_kb.coding.uncertainty.nli_client import fixture_judge

    return fixture_judge(units[name]["records"])


def _corpus(
    n_clear: int = 18, n_ambiguous: int = 2
) -> tuple[np.ndarray, list[ReviewSignal], list[str]]:
    """Corpus of 20 units: 18 clear (wide margin) + 2 ambiguous (thin margin).

    Ambiguous units land in the flagged minority (10%); the escalation band
    check in test_escalation_proportion_uses_flagged_minority uses a corpus
    sized so the band is discriminating.
    """
    scores, signals, reasons = [], [], []
    for i in range(n_clear):
        scores.append([0.90, 0.20 + 0.01 * (i % 5), 0.10])
        signals.append(ReviewSignal(margin=0.70, hedge=0, link_score=0.95))
        reasons.append("explicit")
    for _ in range(n_ambiguous):
        scores.append([0.55, 0.52, 0.10])
        signals.append(ReviewSignal(margin=0.03, hedge=4, link_score=0.40, question_dependent=True))
        reasons.append("explicit")
    return np.array(scores, dtype=np.float32), signals, reasons


def test_escalation_selects_flagged_minority_in_band():
    for n, ambiguous in ((20, 2), (50, 5), (100, 10)):
        signals = [ReviewSignal(margin=0.03 if i < ambiguous else 0.70) for i in range(n)]
        from corpus_kb.coding.uncertainty.review_priority import priority_scores

        preliminary = priority_scores(signals)
        chosen = select_escalation(preliminary)
        proportion = len(chosen) / n
        assert ESCALATION_BAND[0] <= proportion <= ESCALATION_BAND[1] + 1e-9
        assert set(chosen) == set(range(ambiguous))


def test_escalation_proportion_uses_flagged_minority():
    scores, signals, reasons = _corpus()
    units = _fixture_units()
    resample = {
        18: units["ambiguous_two_themes"]["rationales"],
        19: units["clear_denial"]["rationales"],
    }
    result = run_cascade(
        scores,
        signals,
        resample_fn=lambda idx: resample[idx],
        nli_fn=lambda a, b: _judge_for_nli(a, b, resample, units),
        decision_reasons=reasons,
        delta_amb=0.05,
    )
    assert result.n_units == 20
    assert result.n_escalated == 2
    assert 0.10 <= result.escalation_proportion <= 0.20
    assert result.n_nli_calls == 2 * 10


def _judge_for_nli(a, b, resample, units):
    """Dispatch a fixture judgment by locating which unit's pair it is."""
    for name in ("ambiguous_two_themes", "clear_denial"):
        rationales = units[name]["rationales"]
        if a in rationales and b in rationales:
            return _judge_for(units, name)(a, b)
    raise KeyError("pair not in recorded fixtures")


def test_ambiguous_units_rank_above_clear_and_reasons_closed():
    scores, signals, reasons = _corpus()
    units = _fixture_units()
    resample = {
        18: units["ambiguous_two_themes"]["rationales"],
        19: units["clear_denial"]["rationales"],
    }
    result = run_cascade(
        scores,
        signals,
        resample_fn=lambda idx: resample[idx],
        nli_fn=lambda a, b: _judge_for_nli(a, b, resample, units),
        decision_reasons=reasons,
        delta_amb=0.05,
    )
    by_index = {r.unit_index: r for r in result.routed}
    assert by_index[18].priority > by_index[0].priority
    assert by_index[18].escalated is True
    assert by_index[18].semantic_entropy == pytest.approx(_se_of(units, "ambiguous_two_themes"))
    assert by_index[0].semantic_entropy is None
    assert by_index[0].reason == "gray_zone"
    for routed in result.routed:
        assert routed.reason in REVIEW_REASONS


def test_overlap_routing_fires_below_calibrated_delta():
    scores, signals, reasons = _corpus(n_clear=2, n_ambiguous=2)
    units = _fixture_units()
    amb = units["ambiguous_two_themes"]["rationales"]
    clr = units["clear_denial"]["rationales"]
    resample = {2: amb, 3: clr}
    result = run_cascade(
        scores,
        signals,
        resample_fn=lambda idx: resample[idx],
        nli_fn=lambda a, b: _judge_for_nli(a, b, resample, units),
        decision_reasons=reasons,
        delta_amb=0.05,
    )
    by_index = {r.unit_index: r for r in result.routed}
    assert by_index[2].reason == "overlap"
    assert by_index[3].reason == "overlap"
    assert by_index[0].reason == "gray_zone"
    assert set(result.reasons) <= REVIEW_REASONS


def test_se_clear_unit_is_zero_and_ambiguous_unit_positive():
    units = _fixture_units()
    amb, clr = units["ambiguous_two_themes"]["rationales"], units["clear_denial"]["rationales"]

    def resample_fn(idx: int) -> list[str]:
        return amb if idx == 0 else clr

    scores = np.zeros((20, 3), dtype=np.float32)
    scores[0] = [0.55, 0.52, 0.10]
    scores[1] = [0.60, 0.50, 0.10]
    signals = [ReviewSignal(margin=0.03, hedge=4, question_dependent=True)]
    signals.append(ReviewSignal(margin=0.10, hedge=3, link_score=0.40))
    signals.extend(ReviewSignal(margin=0.80, hedge=0, link_score=0.95) for _ in range(18))
    result = run_cascade(
        scores,
        signals,
        resample_fn=resample_fn,
        nli_fn=lambda a, b: _judge_for_nli(a, b, {0: amb, 1: clr}, units),
        decision_reasons=["explicit"] * 20,
        delta_amb=0.05,
    )
    by_index = {r.unit_index: r for r in result.routed}
    escalated = sorted(r.unit_index for r in result.routed if r.escalated)
    assert escalated == [0, 1]
    se_ambiguous = by_index[0].semantic_entropy
    assert se_ambiguous is not None and se_ambiguous > 0.0
    assert by_index[1].semantic_entropy == pytest.approx(0.0)


def _se_of(units: dict[str, dict], name: str) -> float:
    from corpus_kb.coding.uncertainty.tier3_consistency import self_consistency

    judge = _judge_for(units, name)
    return self_consistency(units[name]["rationales"], judge).semantic_entropy
