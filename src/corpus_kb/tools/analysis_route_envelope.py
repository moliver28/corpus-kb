"""The ``analysis_route`` envelope grammar (routing W3 grammar, W5 split).

Status is ``resolved | ambiguous | degraded``; a degraded answer also
shows ``intent.note == "degraded"``; the ambiguous ladder rides
``intent.status == "ambiguous"`` (degraded or not), so
``interpretations`` appear exactly then. A decided answer carries
``suggestions``: up to three verbatim next questions from the chosen
method's register row. An expert override forces ``resolved`` with
``intent.note == "expert_override"``; its suggestions ride the override
record. Routing W5's flag-on LLM fallback is the one note exception: a
degraded run whose tie the fallback resolved shows envelope status
``degraded`` with ``intent.note == "llm_fallback"`` — the status
describes the run, the note describes how the intent resolved, and the
chips ride the resolved intent.

Both builders are pure: register lookups, the rubric join, and
dataclass reshapes only — no embedder, no LLM, no config. Stable key
order keeps the bytes deterministic for the golden snapshot.
"""

from __future__ import annotations

from dataclasses import asdict, replace

from ..analysis.method_register import (
    MethodRecommendation,
    MethodRecord,
    MethodRegister,
)
from ..analysis.method_register_rubric import rank_methods
from ..analysis.question_intent import QuestionIntent
from ..analysis.question_router import RouteResult
from .analysis_route_suggestions import attach_suggestions

__all__ = ["build_envelope", "build_override_envelope", "record_for"]

_READINGS_CAP = 3
_RANKED_CAP = 5
_OVERRIDE_NOTE = "expert_override"
_OVERRIDE_WHY = "Set by the analyst."


def record_for(register: MethodRegister, method_id: str) -> MethodRecord | None:
    """The register row with this id, or ``None``."""
    for record in register.methods:
        if record.id == method_id:
            return record
    return None


def _normalized(text: str) -> str:
    """Casefold and collapse whitespace runs to single spaces."""
    return " ".join(text.split()).casefold()


def _matched_phrase(question: str, record: MethodRecord) -> str | None:
    """The first authored phrase the question contains (authored order)."""
    normalized = _normalized(question)
    for phrase in record.question_phrases or []:
        if _normalized(phrase) in normalized:
            return phrase
    return None


def _row(record: MethodRecord, why: str) -> dict[str, object]:
    """One ranked row: id, name, why, and the run hint."""
    return {
        "method_id": record.id,
        "name": record.name,
        "why": why,
        "run_hint": {"op": record.op, "params": {}},
    }


def _ranked_rows(question: str, ranked: list[MethodRecommendation]) -> list[dict[str, object]]:
    """Ranked rows: the top row carries the matched phrase, the rest the
    rubric's ``rank_reason``. With no phrase hit (the fallback pool), the
    top why is the rubric reason too — a name is never a reason."""
    rows: list[dict[str, object]] = []
    for index, record in enumerate(ranked):
        if index == 0:
            matched = _matched_phrase(question, record)
            why = matched if matched is not None else record.rank_reason
            rows.append(_row(record, why))
        else:
            rows.append(_row(record, record.rank_reason))
    return rows


def _rubric_reason(register: MethodRegister, method_id: str) -> str:
    """The rubric's ``rank_reason`` for this id (the ONE rubric, full join).

    The router caps its rubric join at five, so a row outside the cap
    still needs its reason from a full join. Same register, same bytes.
    """
    for recommendation in rank_methods(register):
        if recommendation.id == method_id:
            return recommendation.rank_reason
    raise KeyError(method_id)  # unreachable: the id came from this register


def build_envelope(
    question: str, register: MethodRegister, result: RouteResult
) -> dict[str, object]:
    """The shared envelope: stable key order, deterministic bytes."""
    ranked = _ranked_rows(question, result.ranked)
    chosen = (
        record_for(register, result.intent.method_id)
        if result.intent.method_id is not None
        else None
    )
    if chosen is not None and all(row["method_id"] != chosen.id for row in ranked):
        # The capped rubric list dropped the score winner (the no-phrase-
        # hit pool is every phrased row). The chosen method gets its row
        # after the capped five: the rubric-order claim for the first
        # rows stays honest, and the resolved answer always shows its
        # method with a name, a why, and a run hint. The why follows the
        # top-row rule: the matched phrase when one hits, the rubric's
        # rank_reason otherwise.
        matched = _matched_phrase(question, chosen)
        why = matched if matched is not None else _rubric_reason(register, chosen.id)
        ranked.append(_row(chosen, why))
    envelope: dict[str, object] = {
        "status": "degraded" if result.degraded else result.status,
        "intent": result.intent.to_envelope(),
        "ranked": ranked,
    }
    if result.intent.status == "ambiguous":
        envelope["interpretations"] = result.intent.readings[:_READINGS_CAP]
    # The chips ride the same chosen record, so the appended row and
    # the chips can never disagree about the chosen method.
    attach_suggestions(envelope, chosen)
    envelope["trace"] = asdict(result.trace)
    return envelope


def build_override_envelope(
    question: str, result: RouteResult, record: MethodRecord
) -> dict[str, object]:
    """The override envelope: the chosen method first, status ``resolved``.

    The trace stays the automatic pass, so the audit shows what the
    cascade scored before the analyst chose. The intent claims no router
    confidence: the analyst decided, so ``confidence`` stays at ``0.0``.
    """
    rest = [rec for rec in result.ranked if rec.id != record.id][: _RANKED_CAP - 1]
    rows = [_row(record, _OVERRIDE_WHY)]
    rows.extend(_row(rec, rec.rank_reason) for rec in rest)
    intent = _override_intent(result.intent, record)
    envelope: dict[str, object] = {
        "status": "resolved",
        "intent": intent.to_envelope(),
        "ranked": rows,
    }
    # The override forced the intent resolved with this method's id, so
    # the chips ride the override record's own suggestions.
    attach_suggestions(envelope, record)
    envelope["trace"] = asdict(result.trace)
    return envelope


def _override_intent(base: QuestionIntent, record: MethodRecord) -> QuestionIntent:
    """The override intent: resolved, chosen first, no readings."""
    return replace(
        base,
        status="resolved",
        confidence=0.0,
        method_id=record.id,
        ranked_method_ids=[
            record.id,
            *[mid for mid in base.ranked_method_ids if mid != record.id],
        ],
        readings=[],
        note=_OVERRIDE_NOTE,
    )
