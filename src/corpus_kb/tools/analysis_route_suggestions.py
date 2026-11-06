"""The ``suggestions`` envelope key for the question router (routing W4).

A decided answer suggests next questions: up to three verbatim strings
from the chosen method's ``suggests`` register row. Presence rides the
intent, not the envelope status (spec: docs/research/analysis-question-
routing-program.md): a degraded but decided answer keeps its chips, an
ambiguous answer has none (the ladder is the answer), and an override
suggests from the override record. A row with no ``suggests`` omits the
key — never an empty list.
"""

from __future__ import annotations

from ..analysis.method_register import MethodRecord

__all__ = ["attach_suggestions"]

_SUGGESTIONS_CAP = 3


def _suggestions_for(record: MethodRecord) -> list[str]:
    """Up to three verbatim next questions from the register row.

    The register schema already caps ``suggests`` at one to three, so the
    slice here is defensive only. The strings pass through untouched.
    """
    if not record.suggests:
        return []
    return list(record.suggests)[:_SUGGESTIONS_CAP]


def attach_suggestions(envelope: dict[str, object], record: MethodRecord | None) -> None:
    """Attach the chosen method's suggestions when a method was decided.

    The caller passes the record whose strings ride: the row matching
    the intent's chosen id on the automatic path (the score winner, not
    the rubric-top row), the override record on the override path. The
    intent inside the envelope decides presence.
    """
    intent = envelope["intent"]
    if not isinstance(intent, dict):
        return
    if intent.get("status") != "resolved" or intent.get("method_id") is None:
        return
    if record is None:
        return
    suggestions = _suggestions_for(record)
    if suggestions:
        envelope["suggestions"] = suggestions
