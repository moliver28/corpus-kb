"""Tests for the persona meter gate (issue #441, zero-drift rule 10).

The meter walks every register method through the offline router in the
doctor's build order. The tests pin the phase derivation, the visit
order, the counting rules, the report schema, and the gated comparison.
"""

from __future__ import annotations

from dataclasses import replace

from corpus_kb.analysis.method_register_loader import load_method_register
from scripts.persona_meter import HEALTHY_STATE, WALK_CONFIG, run_walk, visit_order, walk_phases


def _register():
    return load_method_register({})  # repo config/analysis_methods.yaml


def test_phases_follow_the_doctor_build_chain():
    phases = walk_phases(_register(), ollama_ok=False)
    # Phase 0 is the measured healthy snapshot, never a probe.
    assert phases[0].state == HEALTHY_STATE
    # The chain stops at the first terminal phase, so exactly 4 phases:
    # labels (04), topics (09 — id-tiebreak over 10), predictions (16),
    # terminal.
    assert [p.terminal for p in phases] == [False, False, False, True]
    assert [p.recommended.id if p.recommended else None for p in phases] == [
        "04",
        "09",
        "16",
        None,
    ]


def test_each_phase_applies_one_build_product():
    phases = walk_phases(_register(), ollama_ok=False)
    assert phases[1].state == replace(phases[0].state, labels=120)
    assert phases[2].state == replace(phases[1].state, topics=120)
    assert phases[3].state == replace(phases[2].state, predictions=120)
    assert phases[3].terminal


def test_llm_gated_methods_never_reach_runnable_offline():
    phases = walk_phases(_register(), ollama_ok=False)
    ids = {r.id for p in phases for r in p.runnable}
    assert "23" not in ids and "24" not in ids  # search/ask need llm


def test_visit_order_covers_every_method_once():
    register = _register()
    order = visit_order(register, walk_phases(register, ollama_ok=False))
    assert sorted(order) == [f"{i:02d}" for i in range(1, 31)]
    assert len(set(order)) == 30


def test_visit_order_matches_the_phase_derivation():
    register = _register()
    phases = walk_phases(register, ollama_ok=False)
    order = visit_order(register, phases)
    seen: set[str] = set()
    runnable_once: list[str] = []
    for phase in phases:
        for row in phase.runnable:
            if row.id not in seen:
                seen.add(row.id)
                runnable_once.append(row.id)
    tail = [rid for rid in order if rid not in seen]
    assert tail == sorted(tail)
    assert order == runnable_once + tail


def test_visit_order_is_deterministic():
    register = _register()
    assert visit_order(register, walk_phases(register, ollama_ok=False)) == visit_order(
        register, walk_phases(register, ollama_ok=False)
    )


async def test_walk_never_constructs_the_ollama_embedder(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("OllamaEmbedder constructed during the offline walk")

    # Patch the SOURCE of the lazy import: _router_embedder does
    # `from ..rag.embedder import OllamaEmbedder` inside the function body.
    monkeypatch.setattr("corpus_kb.rag.embedder.OllamaEmbedder", _boom)
    assert WALK_CONFIG["embedding"]["provider"] != "ollama"
    report = await run_walk(_register(), WALK_CONFIG)
    assert len(report.metrics) == 30


async def test_every_method_reaches_success():
    report = await run_walk(_register(), WALK_CONFIG)
    assert all(m.success for m in report.metrics.values())


async def test_steps_count_every_call_before_success():
    report = await run_walk(_register(), WALK_CONFIG)
    order = report.order
    # The walk caps a row at three calls (two phrase tries plus one
    # override) and exactly one call resolves it: steps == failed + 1.
    # The first row carries no prefix, so its steps are its own calls.
    first = report.metrics[order[0]]
    assert first.phrase_resolution_steps in (1, 2, 3)
    assert first.failed_attempts == first.phrase_resolution_steps - 1
    # Every later method resolves strictly after the previous one.
    running = 0
    for method_id in order:
        metrics = report.metrics[method_id]
        if running:
            assert metrics.phrase_resolution_steps > running
        running = metrics.phrase_resolution_steps
