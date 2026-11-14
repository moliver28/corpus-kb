"""Progression vocabularies and validation for the analysis method register.

The novice-UX program (wave 1) adds four optional guidance fields to each
``MethodRecord`` row: ``difficulty`` (one ladder rung), ``concepts`` (the
NLP ideas the method teaches), ``prerequisites`` (method ids whose stored
build must exist first), and ``next`` (method ids that build on this one).
This module holds the words and the checks; the register model calls
:func:`validate_progression_fields` per row and
:func:`validate_progression_graph` over the whole row set. No pydantic
here: the register model owns the schema, this module owns the content,
mirroring ``method_register_routing``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

DIFFICULTY_LEVELS = ("novice", "intermediate", "advanced")

TEACHING_CONCEPTS = (
    "active_learning",
    "classification",
    "clustering",
    "contradiction_detection",
    "entity_extraction",
    "information_retrieval",
    "keyword_rules",
    "qualitative_coding",
    "sentiment_analysis",
    "term_weighting",
    "topic_modeling",
    "vector_similarity",
    "weak_supervision",
)

MAX_CONCEPTS = 5
MAX_LINKS = 5

_ID_SHAPE = re.compile(r"[0-9]{2}")


class ProgressionRecord(Protocol):
    """The progression-field surface the checks read."""

    id: str
    difficulty: str | None
    concepts: list[str] | None
    prerequisites: list[str] | None
    next: list[str] | None


def validate_progression_fields(record: ProgressionRecord) -> None:
    """Validate one row's progression fields. A ``None`` value always passes."""
    if record.difficulty is not None and record.difficulty not in DIFFICULTY_LEVELS:
        raise ValueError(
            f"difficulty: {record.difficulty!r} is not supported. "
            f"Use one of: {', '.join(DIFFICULTY_LEVELS)}."
        )
    _check_concepts(record.concepts)
    _check_links(record.id, "prerequisites", record.prerequisites)
    _check_links(record.id, "next", record.next)


def _check_concepts(concepts: list[str] | None) -> None:
    """Taught concepts: 1 to 5, known words, non-empty, unique."""
    if concepts is None:
        return
    if not 1 <= len(concepts) <= MAX_CONCEPTS:
        raise ValueError(f"concepts: {len(concepts)} found. List 1 to {MAX_CONCEPTS}.")
    unknown = [concept for concept in concepts if concept not in TEACHING_CONCEPTS]
    if unknown:
        raise ValueError(
            f"concepts: {', '.join(unknown)} is not supported. "
            f"Use one of: {', '.join(TEACHING_CONCEPTS)}."
        )
    if any(not concept.strip() for concept in concepts):
        raise ValueError("concepts: every entry must be non-empty text.")
    if len(set(concepts)) != len(concepts):
        raise ValueError("concepts: remove duplicate concepts.")


def _check_links(method_id: str, field_name: str, links: list[str] | None) -> None:
    """Method-id links: up to 5, zero-padded ids, unique, never the row itself.

    An empty list is valid and means "no links" — most methods have none.
    """
    if not links:
        return
    if len(links) > MAX_LINKS:
        raise ValueError(f"{field_name}: {len(links)} found. List up to {MAX_LINKS}.")
    for link in links:
        if not _ID_SHAPE.fullmatch(link) or not 1 <= int(link) <= 30:
            raise ValueError(
                f"{field_name}: {link!r} is not a method id. "
                'Use zero-padded string ids "01" to "30".'
            )
    if len(set(links)) != len(links):
        raise ValueError(f"{field_name}: remove duplicate method ids.")
    if method_id in links:
        raise ValueError(f"{field_name}: method {method_id} cannot list its own id.")


def validate_progression_graph(methods: Sequence[ProgressionRecord]) -> None:
    """Register-level checks: every link names a registered id; no cycles."""
    ids = {record.id for record in methods}
    for record in methods:
        for field_name in ("prerequisites", "next"):
            for target in getattr(record, field_name) or []:
                if target not in ids:
                    raise ValueError(
                        f"{field_name}: {target!r} is not a registered method id "
                        f"(row {record.id}). Registered ids: {', '.join(sorted(ids))}."
                    )
    _reject_cycles(methods, "prerequisites")
    _reject_cycles(methods, "next")


def _reject_cycles(methods: Sequence[ProgressionRecord], field_name: str) -> None:
    """Depth-first cycle check over one link field (edges: row -> target)."""
    edges = {record.id: list(getattr(record, field_name) or []) for record in methods}
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
                f"{field_name}: the links form a cycle: {' -> '.join(cycle)}. Remove one link."
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
