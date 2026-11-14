"""Chat sessions: the rung-agnostic turn loop and the process manager.

``ChatSession`` drives ONE conversation over the small ``AgentClient``
surface (connect/query/receive_response/interrupt/disconnect) plus an
injected ``TurnStream`` — the rung's message-to-event adapter. The claude
rung passes the SDK client; the ollama rung passes a no-op client whose
turn stream runs the ask machinery. Tests drive both through the same
Protocol with a fake client; no live Claude, no network.

One operation, one handler: a turn is exactly one ``TurnStream`` run; the
session adds the single-flight lock, the idle clock, and the receipt.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import suppress
from typing import Protocol

from corpus_kb.chat.events import ChatEvent
from corpus_kb.errors import ConflictError

__all__ = ["AgentClient", "ChatManager", "ChatSession", "TurnStream"]


class AgentClient(Protocol):
    """The only client surface ``session.py`` touches."""

    async def connect(self) -> None: ...

    async def query(self, prompt: str) -> None: ...

    def receive_response(self) -> AsyncIterator[object]: ...

    async def interrupt(self) -> None: ...

    async def disconnect(self) -> None: ...


TurnStream = Callable[
    [AgentClient, str, list[dict[str, str]], int | None],
    AsyncIterator[ChatEvent],
]


class ChatSession:
    """One conversation: the rung client, the lock, the idle clock.

    ``history`` is the multi-turn prompt history the ollama rung replays;
    the claude rung ignores it (the CLI keeps its own transcript on disk).
    """

    def __init__(
        self,
        session_id: str,
        backend: str,
        client: AgentClient,
        turn_stream: TurnStream,
        ttl_s: int,
    ) -> None:
        self.session_id = session_id
        self.backend = backend
        self.history: list[dict[str, str]] = []
        self._client = client
        self._turn_stream = turn_stream
        self._ttl_s = ttl_s
        self._lock = asyncio.Lock()
        self._closed = False
        self.last_activity = time.monotonic()

    async def send(self, text: str, k: int | None = None) -> AsyncGenerator[ChatEvent, None]:
        """Run one turn. A second send while one runs is a typed conflict.

        ``k`` is the per-message retrieval width; the claude rung ignores
        it (the agent sizes its own tool calls), the ollama rung passes
        it to the evidence step.
        """
        if self._closed:
            raise ConflictError("This chat session is closed. Start a new session.")
        if self._lock.locked():
            raise ConflictError("One turn runs at a time. Wait for the current turn.")
        async with self._lock:
            self.touch()
            try:
                async for event in self._turn_stream(self._client, text, self.history, k):
                    yield event
            finally:
                self.touch()

    async def interrupt(self) -> None:
        """Ask the rung to stop the current turn. The pending receipt still
        drains, so the UI gets an honest stop record."""
        await self._client.interrupt()

    async def aclose(self) -> None:
        """Close the session for good; the rung client disconnects.

        Idempotent: the SSE pump and the disconnect handler can both
        close in the same race; the second call is a no-op.
        """
        if self._closed:
            return
        self._closed = True
        await self._client.disconnect()

    def touch(self) -> None:
        """Mark the session active now."""
        self.last_activity = time.monotonic()

    def expired(self, now: float | None = None) -> bool:
        """True when the session sat idle past its TTL."""
        if self._ttl_s <= 0:
            return False
        clock = time.monotonic() if now is None else now
        return (clock - self.last_activity) > self._ttl_s


class ChatManager:
    """The process-wide session table with the ``max_sessions`` cap."""

    def __init__(self, max_sessions: int, ttl_s: int) -> None:
        self._max_sessions = max(1, max_sessions)
        self._ttl_s = ttl_s
        self._sessions: dict[str, ChatSession] = {}

    async def create(self, session: ChatSession) -> ChatSession:
        """Register one session; over the cap is a typed conflict.

        Creating with the id of a session that is still live answers the
        live one and disconnects the duplicate spawn: one owner resuming
        their own session never fights the cap (the page reload). A
        closed leftover under the same id is popped first.
        """
        existing = self._sessions.get(session.session_id)
        if existing is not None:
            if not existing._closed:
                await session.aclose()
                return existing
            await self.close(existing.session_id)
        if len(self._sessions) >= self._max_sessions:
            raise ConflictError("A chat session is already active. Stop it first.")
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> ChatSession | None:
        """The live session with this id, or None.

        Expired sessions still here answer None; the caller sweeps (the
        route layer awaits :meth:`sweep` before each create/get) to close
        their clients honestly.
        """
        session = self._sessions.get(session_id)
        if session is not None and session.expired():
            return None
        return session

    def live_ids(self) -> list[str]:
        """The ids of the sessions that are open right now."""
        return [sid for sid, s in self._sessions.items() if not s.expired()]

    async def close(self, session_id: str) -> bool:
        """Close one session by id; True when it existed."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.aclose()
        return True

    async def sweep(self) -> list[str]:
        """Close the idle sessions past their TTL; return their ids."""
        expired = [sid for sid, s in self._sessions.items() if s.expired()]
        for sid in expired:
            await self.close(sid)
        return expired

    async def close_all(self) -> list[str]:
        """Interrupt and close every session; return their ids.

        The server's shutdown path calls this so no rung subprocess
        outlives the app: the interrupt stops a live turn, the close
        disconnects the client (which kills the CLI subprocess).
        """
        ids = list(self._sessions)
        for sid in ids:
            session = self._sessions.get(sid)
            if session is not None:
                with suppress(Exception):
                    await session.interrupt()
            await self.close(sid)
        return ids
