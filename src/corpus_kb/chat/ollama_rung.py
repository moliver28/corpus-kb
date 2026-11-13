"""The ollama rung: the ask machinery re-plumbed multi-turn.

Reuse, not a second pipeline (rule 1): the SAME retrieval step
(``ask/steps.retrieve_evidence``), the SAME generation seam
(``handlers/llm_handler.LlmHandler.chat_stream``), the SAME citation
filter (``ask/streaming.GroundedEmitter``), and the SAME system prompt
(``ask/composer.ASK_SYSTEM_PROMPT``) the /api/ask surface uses. The ask
op itself is untouched. History is the last ``chat.ollama.history_turns``
turns, kept on the session.

The rung is a fallback, and honest about it: the receipt names the model
that answered, and a failed generation yields a warning plus a failed
envelope — never a fake answer.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from typing import cast
from uuid import uuid4

import asyncpg

from corpus_kb.ask.composer import ASK_SYSTEM_PROMPT, DEFAULT_TENANT_UUID, build_prompt
from corpus_kb.chat.agent_tools import citations_from_sources
from corpus_kb.chat.events import (
    ChatDelta,
    ChatEnvelope,
    ChatEvent,
    ChatWarning,
)
from corpus_kb.chat.session import AgentClient, ChatSession, TurnStream
from corpus_kb.chat.settings import ChatSettings

__all__ = ["build_session"]


class _NoopClient:
    """The client surface for a rung with no subprocess to manage."""

    async def connect(self) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self._pending = prompt

    def receive_response(self) -> AsyncIterator[object]:
        return _empty_stream()

    async def interrupt(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None


async def _empty_stream() -> AsyncIterator[object]:
    return
    yield None  # pragma: no cover — marks the function a generator


def build_session(
    settings: ChatSettings,
    config: dict[str, object],
    pg_pool: asyncpg.Pool,
    resume: str | None = None,
) -> ChatSession:
    """Create one ollama-rung session; no spawn, no login, no network."""
    session_id = resume or str(uuid4())
    return ChatSession(
        session_id=session_id,
        backend="ollama",
        client=_NoopClient(),
        turn_stream=_turn_stream(config, pg_pool, settings.ollama.history_turns, session_id),
        ttl_s=settings.session_ttl_s,
    )


def _turn_stream(
    config: dict[str, object], pg_pool: asyncpg.Pool, history_turns: int, session_id: str
) -> TurnStream:
    """The ask-machinery turn: retrieve, stream grounded deltas, receipt."""

    async def turn(
        client: AgentClient,
        text: str,
        history: list[dict[str, str]],
        k: int | None,
    ) -> AsyncIterator[ChatEvent]:
        from corpus_kb.ask.steps import retrieve_evidence
        from corpus_kb.handlers.llm_handler import LlmHandler
        from corpus_kb.handlers.query_handler import QueryHandler
        from corpus_kb.rag import create_embedder

        started = time.monotonic()
        embedder = create_embedder(config, pg_pool)
        handler = QueryHandler(pg_pool, embedder, config=config)
        _envelope, sources, _degraded = await retrieve_evidence(
            text, handler, DEFAULT_TENANT_UUID, k
        )
        if not sources:
            yield ChatDelta("No evidence matched the question in the corpus.")
            yield _receipt(started, "", [], "empty", session_id)
            _remember(history, text, "", history_turns)
            return
        llm = LlmHandler(config)
        deltas = _generate(text, sources, history, llm)
        if deltas is None:
            yield ChatWarning(
                "The local model is unavailable. Check the llm settings. Then try again."
            )
        kept = ""
        if deltas is not None:
            async for piece in deltas:
                yield piece
                kept += piece.text
        citations = citations_from_sources(sources)
        answer = kept.strip()
        failed = not answer
        if failed:
            yield ChatWarning("The local model did not return a citable answer for this turn.")
        yield _receipt(
            started,
            str(llm.model),
            citations,
            "generation_failed" if failed else "completed",
            session_id,
        )
        _remember(history, text, answer, history_turns)

    return turn


def _generate(
    text: str,
    sources: list[dict[str, object]],
    history: list[dict[str, str]],
    llm: object,
) -> AsyncIterator[ChatDelta] | None:
    """The grounded-delta stream, or None when the seam is missing."""
    from corpus_kb.ask.streaming import GroundedEmitter

    emitter = GroundedEmitter(len(sources))
    prompt_messages: list[dict[str, str]] = [
        {"role": "system", "content": ASK_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": build_prompt(text, sources)},
    ]
    chat_stream = getattr(llm, "chat_stream", None)
    if not callable(chat_stream):
        return None
    stream_llm = cast("Callable[..., AsyncIterator[object]]", chat_stream)

    async def stream() -> AsyncIterator[ChatDelta]:
        failed = False
        async for event in stream_llm(prompt_messages, options={"temperature": 0}):
            if not isinstance(event, dict):
                continue
            if event.get("error"):
                failed = True
                break
            message = event.get("message")
            piece = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(piece, str) and piece:
                out = emitter.feed(piece)
                if out:
                    yield ChatDelta(out)
        if not failed:
            tail = emitter.flush()
            if tail:
                yield ChatDelta(tail)

    return stream()


def _receipt(
    started: float, model: str, citations: list, terminal_reason: str, session_id: str
) -> ChatEnvelope:
    """One honest ollama turn receipt."""
    return ChatEnvelope(
        session_id=session_id,
        backend="ollama",
        num_turns=1,
        duration_ms=int((time.monotonic() - started) * 1000),
        is_error=terminal_reason == "generation_failed",
        terminal_reason=terminal_reason,
        total_cost_usd=None,
        model_usage={"model": model} if model else None,
        model=model,
        citations=citations,
    )


def _remember(history: list[dict[str, str]], text: str, answer: str, history_turns: int) -> None:
    """Keep the last ``history_turns`` exchanges, oldest first."""
    history.append({"role": "user", "content": text})
    if answer:
        history.append({"role": "assistant", "content": answer})
    keep = max(history_turns, 0) * 2
    if keep:
        del history[:-keep]
    else:
        history.clear()
