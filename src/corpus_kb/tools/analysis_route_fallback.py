"""The config-gated LLM fallback for the question router (routing W5).

An ambiguous answer with ``analysis.routing.llm_fallback_enabled`` asks
the ask-op chat seam once (the :class:`corpus_kb.handlers.llm_handler.
LlmHandler` contract). The prompt carries a register summary and the
question; the model answers one JSON object: ``{"method_id", "axes"}``.
The answer must pass a sanity gate: the method id must sit in the
register, and every axis value must sit inside its vocabulary tuple.
A pass resolves the intent with the ``llm_fallback`` note. A fail keeps
the ambiguity. Every attempt appends one JSONL row at the configured
``fallback_log_path`` — the log is the audit trail, pass or fail.

This module is best-effort by design: the routing answer never depends
on the model or on the log write. The router modules stay pure; this
seam lives in the op layer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..analysis.method_register import MethodRegister
from ..analysis.method_register_routing import ROUTING_VOCAB_FIELDS
from ..analysis.question_intent import QuestionIntent
from ..analysis.settings import (
    DEFAULT_ROUTING_FALLBACK_LOG_PATH,
    DEFAULT_ROUTING_LLM_FALLBACK_ENABLED,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FALLBACK_NOTE",
    "FallbackAttempt",
    "RouteChat",
    "append_fallback_log",
    "attempt_fallback",
    "build_messages",
    "fallback_settings",
]

FALLBACK_NOTE = "llm_fallback"


class RouteChat(Protocol):
    """The one seam call: the ask-op chat contract (ask/composer.py).

    The concrete seam is the never-raises ``LlmHandler``: a failure comes
    back as ``{"error": ...}``, never as an exception.
    """

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        options: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class FallbackAttempt:
    """One LLM attempt: the raw text, the outcome, and the new intent.

    ``proposed_method_id`` records what the model named, even when the
    sanity gate refused it, so the audit shows the model's intent.
    ``intent`` is set only on a pass. ``ignored_axes`` names the answer
    keys outside the six vocab tuples: they never reach the intent, and
    the log records them so an omitted axis and junk the model sent stop
    looking alike.
    """

    raw_text: str
    proposed_method_id: str | None
    outcome: str
    intent: QuestionIntent | None
    ignored_axes: tuple[str, ...] = ()


def fallback_settings(routing: dict[str, object]) -> tuple[bool, str]:
    """Read ``(enabled, log path)`` from the ``analysis.routing`` block.

    A missing key keeps its default; ``load_config`` rejects bad values
    at load time, so the readers here see valid types or nothing.
    """
    enabled = routing.get("llm_fallback_enabled", DEFAULT_ROUTING_LLM_FALLBACK_ENABLED)
    path = routing.get("fallback_log_path", DEFAULT_ROUTING_FALLBACK_LOG_PATH)
    return enabled is True, str(path)


def build_messages(question: str, register: MethodRegister) -> list[dict[str, str]]:
    """The chat messages: the contract, the register summary, the question."""
    rows = [f"- {record.id}: {record.name}: {record.summary}" for record in register.methods]
    axes = "; ".join(
        f"{name} ({' | '.join(values)})" for name, values in ROUTING_VOCAB_FIELDS.items()
    )
    system = (
        "You route one research question to one analysis method. "
        'Answer with one JSON object only: {"method_id": string, "axes": object}. '
        f"The axes object may set any of: {axes}. "
        "Use only the listed values. Omit an axis you cannot judge. "
        "Pick one method id from the list."
    )
    user = "Methods:\n" + "\n".join(rows) + f"\n\nQuestion: {question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def attempt_fallback(
    chat: RouteChat,
    question: str,
    register: MethodRegister,
    base: QuestionIntent,
) -> FallbackAttempt:
    """One gated LLM attempt over an ambiguous intent. Deterministic input.

    The chat seam answers with an error dict on any failure (the house
    degrade contract), so this function raises nothing.
    """
    raw = await _chat_text(chat, question, register)
    parsed = _json_object(raw)
    if parsed is None:
        return FallbackAttempt(raw_text=raw, proposed_method_id=None, outcome="failed", intent=None)
    sane = _sane_answer(parsed, register)
    if sane is None:
        proposed = parsed.get("method_id")
        proposed_id = proposed if isinstance(proposed, str) else None
        return FallbackAttempt(
            raw_text=raw, proposed_method_id=proposed_id, outcome="failed", intent=None
        )
    method_id, axes, ignored = sane
    intent = replace(
        base,
        status="resolved",
        confidence=0.0,
        method_id=method_id,
        ranked_method_ids=[method_id, *[mid for mid in base.ranked_method_ids if mid != method_id]],
        readings=[],
        note=FALLBACK_NOTE,
        axes=axes,
    )
    return FallbackAttempt(
        raw_text=raw,
        proposed_method_id=method_id,
        outcome="resolved",
        intent=intent,
        ignored_axes=ignored,
    )


def append_fallback_log(path: str, *, question: str, attempt: FallbackAttempt) -> None:
    """Append one JSONL row for the attempt. Best-effort: never raises.

    An unwritable path logs a warning and moves on — the routing answer
    itself stays honest without the audit row.
    """
    row: dict[str, object] = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "question": question,
        "attempt": attempt.raw_text,
        "outcome": attempt.outcome,
    }
    if attempt.proposed_method_id is not None:
        row["chosen_method_id"] = attempt.proposed_method_id
    if attempt.ignored_axes:
        row["ignored_axes"] = list(attempt.ignored_axes)
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        logger.warning("The routing fallback log at %s is not writable.", path)


async def _chat_text(chat: RouteChat, question: str, register: MethodRegister) -> str:
    """The model's raw text, or ``""`` on any seam failure."""
    response = await chat.chat(build_messages(question, register), options={"temperature": 0})
    if not isinstance(response, dict) or response.get("error"):
        return ""
    message = response.get("message")
    if isinstance(message, dict):
        return str(message.get("content", "") or "")
    return ""


def _json_object(text: str) -> dict[str, object] | None:
    """The first JSON object in the text, or ``None``.

    Code fences and short prose wrappers are tolerated: the slice from
    the first brace to the last brace is what gets parsed.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed: object = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _sane_answer(
    parsed: dict[str, object], register: MethodRegister
) -> tuple[str, dict[str, str | None], tuple[str, ...]] | None:
    """The ``(method id, filled axes, ignored names)`` triple, or ``None``.

    The gate checks exactly two things: the method id sits in the
    register, and every axis value sits inside its vocabulary tuple.
    An absent or empty axis stays ``None`` — no invented value. Keys
    outside the six tuples are dropped (the tuples are the only axis
    check); their names come back third for the audit row.
    """
    method_id = parsed.get("method_id")
    if not isinstance(method_id, str) or not any(row.id == method_id for row in register.methods):
        return None
    raw_axes = parsed.get("axes")
    if raw_axes is None:
        raw_axes = {}
    if not isinstance(raw_axes, dict):
        return None
    ignored = tuple(sorted(set(raw_axes) - set(ROUTING_VOCAB_FIELDS)))
    filled: dict[str, str | None] = {}
    for name, allowed in ROUTING_VOCAB_FIELDS.items():
        value = raw_axes.get(name)
        if value is None or value == "":
            filled[name] = None
            continue
        if isinstance(value, str) and value in allowed:
            filled[name] = value
            continue
        return None
    return method_id, filled, ignored
