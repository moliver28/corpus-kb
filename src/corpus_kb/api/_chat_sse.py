"""Chat SSE body (Phase-2 Wave 7): frames, keep-alives, the close.

Split from ``routes_chat.py`` for the size rule; the route builds the
turn and this module owns the wire. SSE frames come from
``api/_chat_wire.ChatFramer``; disconnect closes the rung client ([D7]):
interrupt, drain the pending receipt, aclose — through the
``ChatManager`` so the closed session deregisters and the cap frees.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from typing import cast

from corpus_kb.api._chat_wire import ChatFramer
from corpus_kb.chat.events import ChatActivity, ChatDelta, ChatEnvelope, ChatWarning
from corpus_kb.chat.session import ChatManager, ChatSession

__all__ = ["chat_sse"]


async def close_turn_session(manager: ChatManager | None, session: ChatSession) -> None:
    """Close one session for good, deregistering it when the manager is live.

    The manager path (``ChatManager.close``) is the one that frees the
    ``max_sessions`` cap slot; the bare ``aclose`` fallback keeps a
    manager-less test or shutdown path working.
    """
    if manager is None:
        await session.aclose()
        return
    await manager.close(session.session_id)


async def chat_sse(
    events: AsyncGenerator[object, None],
    manager: ChatManager | None,
    session: ChatSession,
    framer: ChatFramer,
) -> AsyncIterator[str]:
    """The SSE body: frames, keep-alives, one receipt, ``[DONE]``.

    A pump task moves turn events onto a queue so the writer can idle in
    short waits and emit keep-alive comments during long agent turns. On
    a client disconnect the pump is left running: it drains the pending
    receipt, then closes the session ([D7]).
    """
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    close_after = False

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(("event", event))
            await queue.put(("end", None))
        except Exception as exc:  # the frame below needs any failure
            await queue.put(("error", exc))
        finally:
            await events.aclose()
            if close_after:
                await close_turn_session(manager, session)

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=framer.keep_alive_s)
            except TimeoutError:
                yield framer.keep_alive()
                continue
            if kind == "end":
                break
            if kind == "error":
                raise cast(Exception, payload)
            yield _frame_for(payload, framer)
        yield framer.done()
    except (asyncio.CancelledError, GeneratorExit):
        # Client disconnect ([D7]): stop the turn, drain, then close. A
        # pump still running closes the session after its drain; a pump
        # that already finished leaves the close to this handler.
        close_after = True
        with suppress(Exception):
            await session.interrupt()
        if pump_task.done():
            with suppress(Exception):
                await close_turn_session(manager, session)
        raise
    except Exception as exc:
        # ONE mid-stream error frame, then [DONE] — never a half-open
        # stream; the taxonomy triple matches errors.ERROR_MAP.
        status = getattr(exc, "http_status", 500)
        error_type = getattr(exc, "error_type", "internal_error")
        yield framer.error(int(status), str(exc), str(error_type))
        yield framer.done()
    finally:
        if not close_after:
            pump_task.cancel()
            with suppress(asyncio.CancelledError):
                await pump_task


def _frame_for(event: object, framer: ChatFramer) -> str:
    """One turn event onto the wire."""
    if isinstance(event, ChatDelta):
        return framer.content(event.text)
    if isinstance(event, ChatActivity):
        return framer.activity(event.tool, event.summary, event.chunk_count)
    if isinstance(event, ChatWarning):
        return framer.warning(event.text)
    if isinstance(event, ChatEnvelope):
        return framer.envelope(event.to_payload())
    return framer.error(500, "The turn produced an unknown event.", "internal_error")
