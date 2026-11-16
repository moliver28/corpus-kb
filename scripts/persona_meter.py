"""The persona meter walk (issue #441, zero-drift rule 10).

This script measures the analysis register at the novice-default recipe.
It never runs a database, Ollama, or a CLI subprocess. The walk derives
its phases from the doctor journey seams, visits every register method
once, and routes each row's own seeded phrases through the shared
handler in pinned phrase mode (no embedder, no network). Per method it
records phrase-resolution steps (the gated metric), failed attempts,
and wall time (report-only, never committed). The gate lives in
``scripts/persona_ratchet.py``; the baseline in ``tests/fixtures/``.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from corpus_kb._setup.doctor_journey import _runnable_sorted
from corpus_kb._setup.doctor_journey_maps import BUILD_PRODUCTS
from corpus_kb._setup.doctor_journey_state import JourneyState
from corpus_kb.analysis.method_register import MethodRecord, MethodRegister
from corpus_kb.errors import NoMethodMatchesError, QuestionTooShortError
from corpus_kb.tools.analysis_route_tools import analysis_route

if TYPE_CHECKING:
    import asyncpg

__all__ = [
    "HEALTHY_STATE",
    "WALK_CONFIG",
    "MethodMetrics",
    "WalkPhase",
    "WalkReport",
    "measure_method",
    "run_walk",
    "visit_order",
    "walk_phases",
]

# Pins the router to phrase mode: any provider other than "ollama" means
# the router builds no embedder and touches no network. No "analysis"
# block also means the flag-gated LLM fallback stays off.
WALK_CONFIG: dict[str, object] = {"embedding": {"provider": "none"}}

# The measured healthy snapshot from tests/test_doctor_journey.py. A
# built product sits at 120 in this convention.
HEALTHY_STATE = JourneyState(
    chunks=120, vectors=120, labels=0, predictions=0, topics=0, dated_documents=6
)


@dataclass(frozen=True)
class WalkPhase:
    """One derived walk phase: the state, its runnable rows, the next build."""

    phase: int
    state: JourneyState
    runnable: list[MethodRecord]
    recommended: MethodRecord | None
    terminal: bool


def _build_candidates(
    runnable: list[MethodRecord], state: JourneyState
) -> list[tuple[MethodRecord, str]]:
    """Runnable build rows whose product does not exist yet, with the state key.

    The same filter the doctor's ``_next_check`` uses.
    """
    builds: list[tuple[MethodRecord, str]] = []
    for row in runnable:
        product = BUILD_PRODUCTS.get(row.op) if row.op else None
        if product is not None and getattr(state, product[0]) == 0:
            builds.append((row, product[0]))
    return builds


def walk_phases(register: MethodRegister, ollama_ok: bool = False) -> list[WalkPhase]:
    """Derive the doctor's build chain as walk phases. Deterministic.

    Phase 0 state is ``HEALTHY_STATE``. Each phase computes its runnable
    rows, names the easiest pending build as the recommendation, and the
    next phase applies that one build product. A phase with no pending
    build is terminal and ends the list.
    """
    phases: list[WalkPhase] = []
    state = HEALTHY_STATE
    phase = 0
    while True:
        runnable = _runnable_sorted(register, state, ollama_ok)
        builds = _build_candidates(runnable, state)
        recommended = builds[0][0] if builds else None
        terminal = recommended is None
        phases.append(
            WalkPhase(
                phase=phase,
                state=state,
                runnable=runnable,
                recommended=recommended,
                terminal=terminal,
            )
        )
        if terminal:
            return phases
        state = replace(state, **{builds[0][1]: 120})
        phase += 1


def visit_order(register: MethodRegister, phases: list[WalkPhase]) -> list[str]:
    """Every register method id exactly once, in derived walk order.

    Visit each phase's runnable rows in ``_runnable_sorted`` order and
    keep only the first occurrence of a row across phases. Then visit
    every remaining method id in id order. That tail is the rows that
    never became runnable in any phase.
    """
    order: list[str] = []
    seen: set[str] = set()
    known = {row.id for row in register.methods}
    for phase in phases:
        for row in phase.runnable:
            if row.id not in seen:
                seen.add(row.id)
                order.append(row.id)
    order.extend(rid for rid in sorted(known - seen))
    return order


@dataclass(frozen=True)
class MethodMetrics:
    """One method's walk outcome at the novice-default recipe.

    ``phrase_resolution_steps`` is the gated metric: the 1-based call
    index across the whole walk at which this row resolves. It counts
    how many attempts the row's own seeded phrases need. ``wall_ms`` is
    report-only and never committed.
    """

    success: bool
    phrase_resolution_steps: int
    failed_attempts: int
    wall_ms: int
    override_needed: bool


@dataclass(frozen=True)
class WalkReport:
    """The full walk: derived phases, derived order, per-method metrics."""

    phases: list[WalkPhase]
    order: list[str]
    metrics: dict[str, MethodMetrics]


async def _route(
    config: dict[str, object], question: str, method_id: str | None
) -> dict[str, object]:
    """One handler call with the house no-database pool argument."""
    return await analysis_route(
        cast("asyncpg.Pool", None),  # routing reads no database
        config=config,
        question=question,
        method_id=method_id,
    )


def _resolves_to(result: dict[str, object], method_id: str) -> bool:
    """True when the envelope resolved to exactly this method."""
    if result.get("status") != "resolved":
        return False
    intent = result.get("intent")
    return isinstance(intent, dict) and intent.get("method_id") == method_id


async def measure_method(
    record: MethodRecord, config: dict[str, object], steps_before: int = 0
) -> MethodMetrics:
    """Walk one method: up to two phrase attempts, then one override.

    ``steps_before`` is the walk-wide call count before this row starts.
    A row with no ``question_phrases`` fails loudly. A typed router
    rejection (too short, no match) counts as a failed attempt.
    """
    phrases = record.question_phrases
    if not phrases:
        raise ValueError(f"Method {record.id} carries no question_phrases to walk.")
    failed = 0
    wall = 0.0
    steps = 0
    for index in range(2):
        question = phrases[0] if len(phrases) == 1 else phrases[index]
        started = time.perf_counter()
        try:
            result = await _route(config, question, None)
        except (NoMethodMatchesError, QuestionTooShortError):
            result = {"status": "failed"}
        wall += time.perf_counter() - started
        steps += 1
        if _resolves_to(result, record.id):
            return MethodMetrics(
                success=True,
                phrase_resolution_steps=steps_before + steps,
                failed_attempts=failed,
                wall_ms=int(wall * 1000),
                override_needed=False,
            )
        failed += 1
    started = time.perf_counter()
    try:
        result = await _route(config, phrases[0], record.id)
    except (NoMethodMatchesError, QuestionTooShortError):
        result = {"status": "failed"}
    wall += time.perf_counter() - started
    steps += 1
    return MethodMetrics(
        success=_resolves_to(result, record.id),
        phrase_resolution_steps=steps_before + steps,
        failed_attempts=failed,
        wall_ms=int(wall * 1000),
        override_needed=True,
    )


async def run_walk(register: MethodRegister, config: dict[str, object]) -> WalkReport:
    """Measure every register method in derived order. Deterministic counts.

    The walk visits ``visit_order`` rows. Each row's call count feeds the
    next row's step index, so ``phrase_resolution_steps`` rises strictly
    along the order. Every row must end successful; the override seam
    guarantees it.
    """
    phases = walk_phases(register, ollama_ok=False)
    order = visit_order(register, phases)
    by_id = {row.id: row for row in register.methods}
    metrics: dict[str, MethodMetrics] = {}
    running = 0
    for method_id in order:
        row = by_id[method_id]
        row_metrics = await measure_method(row, config, steps_before=running)
        running = row_metrics.phrase_resolution_steps
        metrics[method_id] = row_metrics
    return WalkReport(phases=phases, order=order, metrics=metrics)


if __name__ == "__main__":  # pragma: no cover - local diagnosis entry point
    from scripts.persona_report import cli

    raise SystemExit(cli(sys.argv[1:]))
