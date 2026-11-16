"""Artifact vocabulary, checks, and ladder derivation for the register.

Each register row declares the artifacts it ``produces`` and ``consumes``
(16-word vocabulary), plus an optional ``after`` teaching order. This
module holds the words, the per-row checks, and the ladder derivation:
a dependency edge exists when one row consumes an artifact another row
produces; ``after`` edges merge in, never replace. The derived views are
deterministic and id-sorted. No pydantic here: the register model owns
the schema, this module owns the content, mirroring
``method_register_progression``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

ARTIFACT_TYPES: tuple[str, ...] = (
    "answers",
    "clusters",
    "codes",
    "entities",
    "evidence",
    "flags",
    "keyphrases",
    "labels",
    "matrices",
    "predictions",
    "proposals",
    "recommendations",
    "scores",
    "summaries",
    "topics",
    "weak_labels",
)

MAX_PRODUCES = 5
MAX_CONSUMES = 5
MAX_AFTER = 5

_ID_SHAPE = re.compile(r"[0-9]{2}")


class DataflowRecord(Protocol):
    """The dataflow-field surface the checks read."""

    id: str
    produces: list[str] | None
    consumes: list[str] | None
    after: list[str] | None


def validate_dataflow_fields(record: DataflowRecord) -> None:
    """Validate one row's dataflow fields. A ``None`` value always passes."""
    _check_artifacts("produces", record.produces, 1)
    _check_artifacts("consumes", record.consumes, 0)
    _check_after(record)


def _check_artifacts(field_name: str, values: list[str] | None, floor: int) -> None:
    """Artifact list: floor to 5 entries, known words, non-empty, unique."""
    if values is None:
        return
    if not floor <= len(values) <= MAX_PRODUCES:
        raise ValueError(f"{field_name}: {len(values)} found. List {floor} to {MAX_PRODUCES}.")
    unknown = [artifact for artifact in values if artifact not in ARTIFACT_TYPES]
    if unknown:
        raise ValueError(
            f"{field_name}: {', '.join(unknown)} is not supported. "
            f"Use one of: {', '.join(ARTIFACT_TYPES)}."
        )
    if any(not artifact.strip() for artifact in values):
        raise ValueError(f"{field_name}: every entry must be non-empty text.")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name}: remove duplicate artifacts.")


def _check_after(record: DataflowRecord) -> None:
    """Teaching order: up to 5, zero-padded ids, unique, never the row itself.

    An empty list is valid and means "no teaching order" — most methods
    have none.
    """
    links = record.after
    if not links:
        return
    if len(links) > MAX_AFTER:
        raise ValueError(f"after: {len(links)} found. List up to {MAX_AFTER}.")
    for link in links:
        if not _ID_SHAPE.fullmatch(link) or not 1 <= int(link) <= 30:
            raise ValueError(
                f'after: {link!r} is not a method id. Use zero-padded string ids "01" to "30".'
            )
    if len(set(links)) != len(links):
        raise ValueError("after: remove duplicate method ids.")
    if record.id in links:
        raise ValueError(f"after: method {record.id} cannot follow itself.")


# ---------------------------------------------------------------------------
# Ladder derivation
# ---------------------------------------------------------------------------


def _producers_by_artifact(methods: Sequence[DataflowRecord]) -> dict[str, set[str]]:
    """Artifact to the ids that declare it in ``produces``."""
    producers: dict[str, set[str]] = {}
    for record in methods:
        for artifact in record.produces or []:
            producers.setdefault(artifact, set()).add(record.id)
    return producers


def _row_by_id(methods: Sequence[DataflowRecord], method_id: str) -> DataflowRecord | None:
    """The row with this id, or ``None`` when the id is not registered."""
    return next((record for record in methods if record.id == method_id), None)


def artifact_prerequisites(methods: Sequence[DataflowRecord], method_id: str) -> list[str]:
    """Producers of what this row consumes; direct only, id-sorted union."""
    row = _row_by_id(methods, method_id)
    if row is None:
        return []
    producers = _producers_by_artifact(methods)
    found: set[str] = set()
    for artifact in row.consumes or []:
        found.update(producers.get(artifact, ()))
    return sorted(found)


def ladder_prerequisites(methods: Sequence[DataflowRecord], method_id: str) -> list[str]:
    """Id-sorted union of artifact prerequisites and the ``after`` order."""
    row = _row_by_id(methods, method_id)
    if row is None:
        return []
    merged = set(artifact_prerequisites(methods, method_id)) | set(row.after or [])
    return sorted(merged)


def ladder_next(methods: Sequence[DataflowRecord], method_id: str) -> list[str]:
    """The id-sorted reverse: rows whose ladder prerequisites hold this id."""
    return sorted(
        record.id for record in methods if method_id in ladder_prerequisites(methods, record.id)
    )


def derive_edges(methods: Sequence[DataflowRecord]) -> list[tuple[str, str]]:
    """(prerequisite, dependent) ladder edges, sorted by (dependent, prerequisite)."""
    edges = {
        (prerequisite, record.id)
        for record in methods
        for prerequisite in ladder_prerequisites(methods, record.id)
    }
    return sorted(edges, key=lambda edge: (edge[1], edge[0]))


def validate_dataflow_graph(methods: Sequence[DataflowRecord]) -> None:
    """Register-level checks: after targets exist; the ladder stays acyclic."""
    ids = {record.id for record in methods}
    for record in methods:
        for target in record.after or []:
            if target not in ids:
                raise ValueError(
                    f"after: {target!r} is not a registered method id "
                    f"(row {record.id}). Registered ids: {', '.join(sorted(ids))}."
                )
    edges = {record.id: ladder_prerequisites(methods, record.id) for record in methods}
    _reject_ladder_cycles(edges)


def _reject_ladder_cycles(edges: dict[str, list[str]]) -> None:
    """Depth-first cycle check over the merged ladder edges."""
    done: set[str] = set()
    visiting: list[str] = []
    on_path: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in on_path:
            start = visiting.index(node)
            cycle = [*visiting[start:], node]
            raise ValueError(
                f"dataflow: the ladder forms a cycle: {' -> '.join(cycle)}. Remove one link."
            )
        visiting.append(node)
        on_path.add(node)
        for target in edges.get(node, ()):
            visit(target)
        visiting.pop()
        on_path.discard(node)
        done.add(node)

    for node in edges:
        visit(node)
