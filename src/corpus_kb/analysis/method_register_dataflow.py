"""Artifact vocabulary and dataflow checks for the analysis method register.

Each register row declares the artifacts it ``produces`` and ``consumes``
(16-word vocabulary), plus an optional ``after`` teaching order. This
module holds the words and the per-row checks; the ladder derivation
joins it in the derivation engine task. No pydantic here: the register
model owns the schema, this module owns the content, mirroring
``method_register_progression``.
"""

from __future__ import annotations

import re
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
