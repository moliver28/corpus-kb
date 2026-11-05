"""MCP/HTTP/socket/CLI handler for the question router (routing W3).

:func:`analysis_route` — the ONE op-layer composition over
:func:`corpus_kb.analysis.question_router.route_question`. It validates
the question, loads the register through the one loader, routes, and
builds the shared envelope. The router outcome ``insufficient`` never
reaches the envelope: it raises :class:`NoMethodMatchesError`.

The envelope grammar (spec: docs/research/analysis-question-routing-
program.md): ``status`` is ``resolved | ambiguous | degraded``; a
degraded answer also shows ``intent.note == "degraded"``; the ambiguous
ladder rides ``intent.status == "ambiguous"`` (degraded or not), so
``interpretations`` appear exactly then. An expert override forces
``resolved`` with ``intent.note == "expert_override"``.

``pg_pool`` is accepted for the house handler signature; routing reads
no database. The embedder follows ``embedding.provider``: the ``ollama``
provider rides the sync ``OllamaEmbedder`` (its zero-vector fallback
keeps an outage honest — the envelope falls back to phrase mode), and
every other provider passes ``None`` today, so those envelopes take the
phrase path and say so in the trace.
"""

from __future__ import annotations

from dataclasses import asdict, replace

import asyncpg

from ..analysis.method_register import (
    MethodRecommendation,
    MethodRecord,
    MethodRegister,
)
from ..analysis.method_register_loader import load_method_register
from ..analysis.question_router import RouteResult, RoutingEmbedder, route_question
from ..errors import NoMethodMatchesError, QuestionTooShortError, ValidationError
from ..utils.config_sections import mapping_section
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["analysis_route"]

_MIN_QUESTION_CHARS = 8
_READINGS_CAP = 3
_RANKED_CAP = 5
_OVERRIDE_NOTE = "expert_override"
_OVERRIDE_WHY = "Set by the analyst."
_SHORT_MESSAGE = "Ask a question of at least 8 characters."
_NO_MATCH_MESSAGE = "No analysis method matches the question. Try other words."


def _check_question(question: object) -> None:
    """Typed question checks (validation family: 400 / -32602 / exit 1)."""
    if not isinstance(question, str):
        raise ValidationError(
            "question: expected text.",
            remediation="Pass the question as a string.",
        )
    if len(question.strip()) < _MIN_QUESTION_CHARS:
        raise QuestionTooShortError(_SHORT_MESSAGE)


def _record_for(register: MethodRegister, method_id: str) -> MethodRecord | None:
    """The register row with this id, or ``None``."""
    for record in register.methods:
        if record.id == method_id:
            return record
    return None


def _check_override(method_id: object, register: MethodRegister) -> MethodRecord | None:
    """The validated override record, or ``None`` when the caller set none.

    An unknown or non-string ``method_id`` is a typed validation error,
    never a silent fallback to the automatic route.
    """
    if method_id is None:
        return None
    record = _record_for(register, method_id) if isinstance(method_id, str) else None
    if record is None:
        raise ValidationError(
            f"method_id: {method_id!r} is not in the register.",
            remediation="Pick a register id, or leave method_id out.",
        )
    return record


def _router_embedder(config: dict[str, object]) -> RoutingEmbedder | None:
    """The sync embedder for the router, or ``None`` (phrase mode).

    The ``ollama`` provider rides the sync ``OllamaEmbedder``; its
    zero-vector fallback keeps an outage honest. Other providers pass
    ``None`` today, so their envelopes take the phrase path.
    """
    provider = str(mapping_section(config, "embedding").get("provider") or "ollama")
    if provider != "ollama":
        return None
    from ..rag.embedder import OllamaEmbedder  # lazy: keeps module load light

    return OllamaEmbedder(config)


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
    rubric's ``rank_reason``."""
    rows: list[dict[str, object]] = []
    for index, record in enumerate(ranked):
        if index == 0:
            matched = _matched_phrase(question, record)
            rows.append(_row(record, matched if matched is not None else record.name))
        else:
            rows.append(_row(record, record.rank_reason))
    return rows


def _envelope(question: str, result: RouteResult) -> dict[str, object]:
    """The shared envelope: stable key order, deterministic bytes."""
    envelope: dict[str, object] = {
        "status": "degraded" if result.degraded else result.status,
        "intent": result.intent.to_envelope(),
        "ranked": _ranked_rows(question, result.ranked),
    }
    if result.intent.status == "ambiguous":
        envelope["interpretations"] = result.intent.readings[:_READINGS_CAP]
    envelope["trace"] = asdict(result.trace)
    return envelope


def _override_envelope(
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
    intent = replace(
        result.intent,
        status="resolved",
        confidence=0.0,
        method_id=record.id,
        ranked_method_ids=[
            record.id,
            *[mid for mid in result.intent.ranked_method_ids if mid != record.id],
        ],
        readings=[],
        note=_OVERRIDE_NOTE,
    )
    envelope: dict[str, object] = {
        "status": "resolved",
        "intent": intent.to_envelope(),
        "ranked": rows,
    }
    envelope["trace"] = asdict(result.trace)
    return envelope


async def analysis_route(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    question: str,
    method_id: str | None = None,
) -> dict[str, object]:
    """Route one plain-language question to an analysis method.

    Returns ``{status, intent, ranked, trace}``; an ambiguous intent adds
    ``interpretations``. Status is ``resolved``, ``ambiguous``, or
    ``degraded``. Deterministic: same question, same register, same bytes.
    """
    del pg_pool, tenant_id  # accepted for the house signature; reads no database
    _check_question(question)
    cfg = dict(config) if isinstance(config, dict) else {}
    register = load_method_register(cfg)
    override = _check_override(method_id, register)
    settings = dict(mapping_section(mapping_section(cfg, "analysis"), "routing")) or None
    result = route_question(question, register, _router_embedder(cfg), settings)
    if override is not None:
        return _override_envelope(question, result, override)
    if result.status == "insufficient":
        raise NoMethodMatchesError(_NO_MATCH_MESSAGE)
    return _envelope(question, result)
