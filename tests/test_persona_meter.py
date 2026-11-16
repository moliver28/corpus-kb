"""Tests for the persona meter gate (issue #441, zero-drift rule 10).

The meter walks every register method through the offline router in the
doctor's build order. The tests pin the phase derivation, the visit
order, the counting rules, the report schema, and the gated comparison.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from corpus_kb.analysis.method_register_loader import load_method_register
from scripts.persona_meter import HEALTHY_STATE, WALK_CONFIG, run_walk, visit_order, walk_phases
from scripts.persona_ratchet import compare_reports
from scripts.persona_report import build_report

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "persona_meter"


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


async def test_report_is_byte_deterministic_without_wall():
    register = _register()
    walk_a = await run_walk(register, WALK_CONFIG)
    walk_b = await run_walk(register, WALK_CONFIG)
    report_a = json.dumps(build_report(register, walk_a, include_wall=False), sort_keys=True)
    report_b = json.dumps(build_report(register, walk_b, include_wall=False), sort_keys=True)
    assert report_a == report_b


async def test_committed_report_has_no_wall_fields():
    data = json.loads((FIXTURES / "report.v1.json").read_text(encoding="utf-8"))
    # `.get` keeps this green on the Task 4 `{}` placeholder (F2); Task 6's
    # real fixture is the strict per-method check.
    for method in data.get("methods", {}).values():
        assert "wall_ms" not in method


def _methods_record(steps: int, failed: int) -> dict[str, object]:
    record = {"phrase_resolution_steps": steps, "failed_attempts": failed, "success": True}
    return {"methods": {"02": record}}


def test_compare_passes_on_equal_and_improved_steps():
    schema = json.loads((FIXTURES / "schema.v1.json").read_text(encoding="utf-8"))
    base = _methods_record(3, 2)
    assert compare_reports(base, base, schema) == []
    better = _methods_record(2, 1)
    assert compare_reports(base, better, schema) == []


def test_compare_fails_on_a_steps_regression():
    schema = json.loads((FIXTURES / "schema.v1.json").read_text(encoding="utf-8"))
    base = _methods_record(3, 2)
    worse = _methods_record(4, 3)
    failures = compare_reports(base, worse, schema)
    assert len(failures) == 1 and "02" in failures[0] and "4" in failures[0]


def test_compare_ignores_wall_time_drift():
    schema = json.loads((FIXTURES / "schema.v1.json").read_text(encoding="utf-8"))
    base = {"methods": {"02": {"phrase_resolution_steps": 3, "wall_ms": 1, "success": True}}}
    live = {"methods": {"02": {"phrase_resolution_steps": 3, "wall_ms": 999, "success": True}}}
    assert compare_reports(base, live, schema) == []


def test_compare_fails_when_the_walk_order_changes():
    schema = json.loads((FIXTURES / "schema.v1.json").read_text(encoding="utf-8"))
    base = {"methods": {}, "novice_walk_order": ["24", "25"]}
    live = {"methods": {}, "novice_walk_order": ["25", "24"]}
    failures = compare_reports(base, live, schema)
    assert len(failures) == 1 and "novice_walk_order" in failures[0]


def test_compare_fails_on_an_undeclared_report_field():
    schema = json.loads((FIXTURES / "schema.v1.json").read_text(encoding="utf-8"))
    base = {"methods": {"02": {"phrase_resolution_steps": 3}}}
    live = {"methods": {"02": {"phrase_resolution_steps": 3, "bogus_field": 7}}}
    failures = compare_reports(base, live, schema)
    assert len(failures) == 1 and "02" in failures[0] and "bogus_field" in failures[0]
