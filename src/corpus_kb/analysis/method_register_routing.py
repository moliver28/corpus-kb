"""Routing vocabularies and validation for the analysis method register.

The question-routing program adds six controlled vocabularies and ten
optional routing fields to each ``MethodRecord`` row. This module holds the
routing words and the checks behind them; the register model calls
:func:`validate_routing_axes` after its core vocabulary checks. No pydantic
here: the register model owns the schema, this module owns the routing
content. The wave-2 question router reads the tuples directly. The
``RoutingAxesRecord`` protocol spells the field surface the checks read,
so this module imports nothing from the register model (CodeQL triage).
"""

from __future__ import annotations

from typing import Protocol

TASK_TYPES = ("representation", "discovery", "measurement", "prediction", "causal_inference")
COMPARISON_STRUCTURES = (
    "none",
    "subset_vs_subset",
    "paired",
    "temporal",
    "cross_corpus",
    "model_vs_model",
)
OUTPUT_FORMS = ("point_answer", "ranked_list", "statistic", "cluster_map", "graph", "narrative")
EVIDENCE_BASES = ("vector", "lexical", "claims", "graph", "labels")
STATISTICAL_RIGOR_LEVELS = ("descriptive", "inferential_null", "inferential_calibrated")
COST_TIERS = ("interactive", "batch")

ROUTING_VOCAB_FIELDS: dict[str, tuple[str, ...]] = {
    "task_type": TASK_TYPES,
    "comparison_structure": COMPARISON_STRUCTURES,
    "output_form": OUTPUT_FORMS,
    "evidence_basis": EVIDENCE_BASES,
    "statistical_rigor": STATISTICAL_RIGOR_LEVELS,
    "cost_tier": COST_TIERS,
}

MAX_QUESTION_PHRASES = 10
MAX_SUGGESTS = 3
MAX_SUGGEST_WORDS = 20


class RoutingAxesRecord(Protocol):
    """The routing-field surface ``validate_routing_axes`` reads."""

    task_type: str | None
    comparison_structure: str | None
    output_form: str | None
    evidence_basis: str | None
    statistical_rigor: str | None
    cost_tier: str | None
    question_phrases: list[str] | None
    suggests: list[str] | None


def validate_routing_axes(record: RoutingAxesRecord) -> None:
    """Validate the optional routing fields. A ``None`` value always passes."""
    for field_name, allowed in ROUTING_VOCAB_FIELDS.items():
        value = getattr(record, field_name)
        if value is not None and value not in allowed:
            raise ValueError(
                f"{field_name}: {value!r} is not supported. Use one of: {', '.join(allowed)}."
            )
    _check_phrases(record.question_phrases)
    _check_suggests(record.suggests)


def _check_phrases(phrases: list[str] | None) -> None:
    """Seed phrases: 1 to 10, non-empty, unique."""
    if phrases is None:
        return
    if not 1 <= len(phrases) <= MAX_QUESTION_PHRASES:
        raise ValueError(
            f"question_phrases: {len(phrases)} phrases found. List 1 to {MAX_QUESTION_PHRASES}."
        )
    if any(not phrase.strip() for phrase in phrases):
        raise ValueError("question_phrases: phrases must be non-empty text.")
    if len(set(phrases)) != len(phrases):
        raise ValueError("question_phrases: remove duplicate phrases.")


def _check_suggests(suggests: list[str] | None) -> None:
    """Follow-up questions: 1 to 3, non-empty, unique, each 20 words or fewer."""
    if suggests is None:
        return
    if not 1 <= len(suggests) <= MAX_SUGGESTS:
        raise ValueError(f"suggests: {len(suggests)} questions found. List 1 to {MAX_SUGGESTS}.")
    if any(not item.strip() for item in suggests):
        raise ValueError("suggests: questions must be non-empty text.")
    if len(set(suggests)) != len(suggests):
        raise ValueError("suggests: remove duplicate questions.")
    for item in suggests:
        words = len(item.split())
        if words > MAX_SUGGEST_WORDS:
            raise ValueError(f"suggests: {words} words. Use {MAX_SUGGEST_WORDS} words or fewer.")
