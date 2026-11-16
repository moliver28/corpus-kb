"""Computed difficulty rungs for the analysis method register (issue 440).

Two static measures feed one computed rung per row: the graph depth in
the derived ladder (#439) and the element interactivity at the
novice-default recipe (user decisions plus taught concepts). The stored
``difficulty`` pin stays authoritative everywhere; the computed rung is
advisory and reaches only the test snapshot and the generated docs
report. No envelope key, no config key, no new register field.

The ladder is acyclic by register validation, so the depth walk is a
memoized longest-path search over the derived edges. The computed rung
is always one of ``DIFFICULTY_LEVELS`` (novice, intermediate, advanced).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from corpus_kb.analysis.method_register import MethodRecord, MethodRegister
from corpus_kb.analysis.method_register_dataflow import derive_edges

# The autonomy axis to required user decisions, at the novice-default
# recipe: the register row itself, run through its declared default op
# path with no calibration overrides (plan finding B-1).
DECISION_WEIGHTS: dict[str, int] = {
    "human_led": 2,
    "human_in_loop": 1,
    "automated_with_audit": 0,
}


@dataclass(frozen=True)
class DifficultyScore:
    """One row's computed measures next to its stored rung."""

    method_id: str
    graph_depth: int
    element_interactivity: int
    computed_rung: str
    stored_rung: str | None
    matches: bool


def graph_depth(methods: Sequence[MethodRecord]) -> dict[str, int]:
    """Longest path from any source row to each row, keyed by id.

    A source is a row with no derived prerequisites; sources sit at
    depth 0. The ladder must be acyclic (register validation holds it).
    """
    incoming: dict[str, list[str]] = {record.id: [] for record in methods}
    for prerequisite, dependent in derive_edges(methods):
        incoming[dependent].append(prerequisite)
    memo: dict[str, int] = {}

    def depth(mid: str) -> int:
        found = memo.get(mid)
        if found is not None:
            return found
        value = 1 + max((depth(parent) for parent in incoming[mid]), default=-1)
        memo[mid] = value
        return value

    return {record.id: depth(record.id) for record in methods}


def element_interactivity(record: MethodRecord) -> int:
    """Required user decisions plus the concepts the row teaches."""
    return DECISION_WEIGHTS[record.autonomy] + len(record.concepts or ())


def computed_rung(depth: int, ei: int) -> str:
    """The rung for a depth and interactivity pair (rules read top-down).

    The six-rule table: depth 4 or more is advanced; interactivity 3 or
    more is advanced at depth 0 and intermediate deeper; interactivity 2
    is novice at depth 0 and intermediate deeper; the rest is novice.
    """
    if depth >= 4:
        return "advanced"
    if ei >= 3:
        return "advanced" if depth == 0 else "intermediate"
    if ei == 2:
        return "novice" if depth == 0 else "intermediate"
    return "novice"


def difficulty_scores(register: MethodRegister) -> list[DifficultyScore]:
    """Every row's scores against its stored pin, id-sorted."""
    depths = graph_depth(register.methods)
    scores: list[DifficultyScore] = []
    for record in register.methods:
        depth = depths[record.id]
        ei = element_interactivity(record)
        rung = computed_rung(depth, ei)
        scores.append(
            DifficultyScore(
                method_id=record.id,
                graph_depth=depth,
                element_interactivity=ei,
                computed_rung=rung,
                stored_rung=record.difficulty,
                matches=record.difficulty == rung,
            )
        )
    return sorted(scores, key=lambda score: score.method_id)


def difficulty_mismatches(register: MethodRegister) -> list[str]:
    """One plain line per pinned rung that differs from the computation.

    The stored rung wins; these lines are advisory. Output is id-sorted,
    one sentence per row, 20 words or fewer, and never a Python warning.
    """
    return [
        f"Method {score.method_id} computes {score.computed_rung}; "
        f"the register pins {score.stored_rung}."
        for score in difficulty_scores(register)
        if not score.matches
    ]
