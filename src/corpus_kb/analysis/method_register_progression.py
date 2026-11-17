"""Progression vocabularies and validation for the analysis method register.

The novice-UX program (wave 1) adds two optional guidance fields to each
``MethodRecord`` row: ``difficulty`` (one ladder rung) and ``concepts``
(the NLP ideas the method teaches). The ladder's order comes from the
dataflow fields; this module holds the rung words, the teaching
concepts, and their checks. The register model calls
:func:`validate_progression_fields` per row. No pydantic here: the
register model owns the schema, this module owns the content, mirroring
``method_register_routing``.
"""

from __future__ import annotations

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


class ProgressionRecord(Protocol):
    """The progression-field surface the checks read."""

    id: str
    difficulty: str | None
    concepts: list[str] | None


def validate_progression_fields(record: ProgressionRecord) -> None:
    """Validate one row's progression fields. A ``None`` value always passes."""
    if record.difficulty is not None and record.difficulty not in DIFFICULTY_LEVELS:
        raise ValueError(
            f"difficulty: {record.difficulty!r} is not supported. "
            f"Use one of: {', '.join(DIFFICULTY_LEVELS)}."
        )
    _check_concepts(record.concepts)


def _check_concepts(concepts: list[str] | None) -> None:
    """Taught concepts: 1 to 5, non-empty, known words, unique."""
    if concepts is None:
        return
    if not 1 <= len(concepts) <= MAX_CONCEPTS:
        raise ValueError(f"concepts: {len(concepts)} found. List 1 to {MAX_CONCEPTS}.")
    # Blank entries come first: the vocabulary check cannot name them.
    if any(not concept.strip() for concept in concepts):
        raise ValueError("concepts: every entry must be non-empty text.")
    unknown = [concept for concept in concepts if concept not in TEACHING_CONCEPTS]
    if unknown:
        raise ValueError(
            f"concepts: {', '.join(unknown)} is not supported. "
            f"Use one of: {', '.join(TEACHING_CONCEPTS)}."
        )
    if len(set(concepts)) != len(concepts):
        raise ValueError("concepts: remove duplicate concepts.")
