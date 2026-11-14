"""MCP/HTTP/socket/CLI handler for the question router (routing W3).

:func:`analysis_route` — the ONE op-layer composition over
:func:`corpus_kb.analysis.question_router.route_question`. It validates
the question, loads the register through the one loader, routes, and
builds the shared envelope. The router outcome ``insufficient`` never
reaches the envelope: it raises :class:`NoMethodMatchesError`. The
envelope grammar itself (status, ladder, suggestions, override) lives
in ``analysis_route_envelope.py``.

``pg_pool`` is accepted for the house handler signature; routing reads
no database. The embedder follows ``embedding.provider``: the ``ollama``
provider rides the sync ``OllamaEmbedder`` (its zero-vector fallback
keeps an outage honest — the envelope falls back to phrase mode), and
every other provider passes ``None`` today, so those envelopes take the
phrase path and say so in the trace.

Routing W5 adds the config-gated LLM fallback (flag OFF by default):
when the route lands ambiguous AND ``analysis.routing.llm_fallback_enabled``
is true, the ask-op chat seam gets ONE sanity-gated attempt to resolve
the tie; every attempt lands in the ``fallback_log_path`` JSONL audit
file. With the flag off the envelopes stay byte-identical to the W4
grammar; the fallback seam lives in ``analysis_route_fallback.py`` and
the router modules stay pure.
"""

from __future__ import annotations

from dataclasses import replace

import asyncpg

from ..analysis.method_register import MethodRecord, MethodRegister
from ..analysis.method_register_loader import load_method_register
from ..analysis.question_router import RouteResult, RoutingEmbedder, route_question
from ..errors import NoMethodMatchesError, QuestionTooShortError, ValidationError
from ..utils.config_sections import mapping_section
from .analysis_route_envelope import build_envelope, build_override_envelope, record_for
from .analysis_route_fallback import (
    RouteChat,
    append_fallback_log,
    attempt_fallback,
    fallback_settings,
)
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["analysis_route"]

_MIN_QUESTION_CHARS = 8
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


def _check_override(method_id: object, register: MethodRegister) -> MethodRecord | None:
    """The validated override record, or ``None`` when the caller set none.

    An unknown or non-string ``method_id`` is a typed validation error,
    never a silent fallback to the automatic route.
    """
    if method_id is None:
        return None
    record = record_for(register, method_id) if isinstance(method_id, str) else None
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


def _route_llm(config: dict[str, object]) -> RouteChat:
    """The chat seam for the LLM fallback: the ask-op ``LlmHandler``.

    Lazy import, same convention as ``_router_embedder``. The handler
    never raises; a failure comes back as an error dict.
    """
    from ..handlers.llm_handler import LlmHandler  # lazy: keeps module load light

    return LlmHandler(config)


async def _maybe_llm_fallback(
    cfg: dict[str, object],
    settings: dict[str, object] | None,
    question: str,
    register: MethodRegister,
    result: RouteResult,
) -> RouteResult:
    """The flag-gated LLM attempt on an ambiguous tie. Best-effort, logged.

    Flag off (or no routing block): the result passes through untouched,
    no seam is built, no file is written. Flag on: exactly one attempt;
    a pass swaps the intent, a fail keeps the ladder. The audit row
    lands either way.
    """
    if settings is None:
        return result
    enabled, log_path = fallback_settings(settings)
    if not enabled:
        return result
    attempt = await attempt_fallback(_route_llm(cfg), question, register, result.intent)
    append_fallback_log(log_path, question=question, attempt=attempt)
    if attempt.intent is not None:
        return replace(result, intent=attempt.intent, status="resolved")
    return result


async def analysis_route(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    question: str,
    method_id: str | None = None,
) -> dict[str, object]:
    """Route one plain-language question to an analysis method.

    Returns ``{status, intent, ranked, next_methods, trace}``; an
    ambiguous intent adds ``interpretations``, and a decided intent adds
    ``suggestions`` from the chosen method. ``next_methods`` carries the
    chosen row's register chain on a resolved answer and ``[]``
    otherwise. Status is ``resolved``, ``ambiguous``, or ``degraded``.
    Deterministic: same question, same register, same bytes.
    """
    del pg_pool, tenant_id  # accepted for the house signature; reads no database
    _check_question(question)
    cfg = dict(config) if isinstance(config, dict) else {}
    register = load_method_register(cfg)
    override = _check_override(method_id, register)
    settings = dict(mapping_section(mapping_section(cfg, "analysis"), "routing")) or None
    result = route_question(question, register, _router_embedder(cfg), settings)
    if override is not None:
        return build_override_envelope(question, register, result, override)
    if result.status == "insufficient":
        raise NoMethodMatchesError(_NO_MATCH_MESSAGE)
    if result.status == "ambiguous":
        result = await _maybe_llm_fallback(cfg, settings, question, register, result)
    return build_envelope(question, register, result)
