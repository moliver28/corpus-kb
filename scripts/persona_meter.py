"""The persona meter walk (issue #441, zero-drift rule 10).

This script measures the analysis register at the novice-default recipe.
It never runs a database, Ollama, or a CLI subprocess. The walk derives
its phases from the doctor journey seams, visits every register method
once in that derived order, and routes each row's own seeded phrases
through the shared handler in pinned phrase mode. Phrase mode means
``embedding.provider`` is not ``ollama``, so the router never builds an
embedder and never touches the network.

The walk records, per method: phrase-resolution steps, failed attempts,
and wall time. Phrase-resolution steps count how many attempts the
row's own seeded phrases need to resolve that row. This is a phrase
specificity proxy. It is not a measure of novice difficulty or
pedagogy; the #440 human review is the only consumer.

``scripts/persona_ratchet.py`` compares the gated fields with the
committed baseline in ``tests/fixtures/persona_meter/``. Determinism
rule: the same tree produces the same report bytes. Wall time is
measured but never committed or compared.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from corpus_kb._setup.doctor_journey import _runnable_sorted
from corpus_kb._setup.doctor_journey_maps import BUILD_PRODUCTS
from corpus_kb._setup.doctor_journey_state import JourneyState
from corpus_kb.analysis.method_register import MethodRecord, MethodRegister

__all__ = ["HEALTHY_STATE", "WalkPhase", "walk_phases"]

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
