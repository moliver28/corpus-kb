"""Offline unit tests for the tier-3 wiring pass (issue #132).

Covers the escalation branches the DB-backed wiring test cannot reach:
an escalated unit with no decisions is counted as skipped (its signal has
no assignment event to ride), never silently dropped and never written.
"""

from __future__ import annotations

from corpus_kb.research.deductive_tier3 import _tier3_review_pass
from corpus_kb.research.run_inputs import UnitViews

TIER3_CFG = {
    "coding": {
        "uncertainty": {
            "tier3_enabled": True,
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
        }
    }
}


def _unit(unit_id: int) -> UnitViews:
    vec = [1.0, 0.0]
    return UnitViews(unit_id, vec, vec, vec, None, None)


async def test_escalated_unit_without_decision_is_counted_skipped(monkeypatch) -> None:
    import corpus_kb.research.deductive_tier3 as tier3_module

    async def fake_resample(
        text: str, name: str, definition: str, n: int, model: str, base_url: str
    ) -> list[str]:
        return [f"sample {i}" for i in range(n)]

    async def fake_nli(a: str, b: str, model: str = "q", base_url: str = "x") -> bool | None:
        return True

    monkeypatch.setattr(tier3_module, "resample_rationales", fake_resample)
    monkeypatch.setattr(tier3_module, "nli_mutual_entailment", fake_nli)

    units = [_unit(1), _unit(2)]
    scored = {"c1": {"answer": [0.9, 0.1], "qa": [0.9, 0.1], "question": [0.9, 0.1]}}
    cascade, signals_by_unit, n_skipped, tier3_on = await _tier3_review_pass(
        TIER3_CFG, [], units, scored, {}
    )
    assert tier3_on is True
    assert cascade.n_escalated >= 1, "the flagged minority still escalates"
    assert signals_by_unit == {}, "no assignment means no signal event to ride"
    assert n_skipped == cascade.n_escalated, "every skipped escalation is counted"


async def test_disabled_tier3_writes_no_signals(monkeypatch) -> None:
    import corpus_kb.research.deductive_tier3 as tier3_module

    async def forbidden(*args: object) -> list[str]:
        raise AssertionError("resampling must not run when tier3 is disabled")

    monkeypatch.setattr(tier3_module, "resample_rationales", forbidden)
    units = [_unit(1), _unit(2)]
    scored = {"c1": {"answer": [0.9, 0.1], "qa": [0.9, 0.1], "question": [0.9, 0.1]}}
    cascade, signals_by_unit, n_skipped, tier3_on = await _tier3_review_pass(
        {"coding": {"uncertainty": {"tier3_enabled": False}}},
        [],
        units,
        scored,
        {},
    )
    assert tier3_on is False
    assert cascade.n_escalated == 0
    assert signals_by_unit == {}
    assert n_skipped == 0
