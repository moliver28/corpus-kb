"""Chat turn events: the dataclasses the route layer serializes.

One turn yields zero or more deltas (text pieces), zero or more
activity lines (tool progress), zero or more warnings (CLI stderr,
verbatim), then exactly one envelope (the turn receipt). The SSE framer
and the JSON receipt both read these; nothing else knows their shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ChatActivity",
    "ChatCitation",
    "ChatDelta",
    "ChatEnvelope",
    "ChatEvent",
    "ChatWarning",
]


@dataclass
class ChatDelta:
    """One grounded text piece of the answer."""

    text: str


@dataclass
class ChatActivity:
    """One tool-progress line ("Searching the corpus...")."""

    tool: str
    summary: str
    chunk_count: int | None = None


@dataclass
class ChatWarning:
    """One CLI warning line, surfaced verbatim (expiry, org policy)."""

    text: str


@dataclass
class ChatCitation:
    """One corpus chunk the answer may cite; a UI chip renders it."""

    chunk_id: str
    document_id: str
    title: str
    source_path: str
    snippet: str
    score: float

    def to_payload(self) -> dict[str, object]:
        """The wire form of one citation chip."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "title": self.title,
            "source_path": self.source_path,
            "snippet": self.snippet,
            "score": self.score,
        }


@dataclass
class ChatEnvelope:
    """The turn receipt: the honest settle record for one sent message."""

    session_id: str
    backend: str
    num_turns: int
    duration_ms: int
    is_error: bool
    terminal_reason: str | None = None
    total_cost_usd: float | None = None
    model_usage: dict[str, object] | None = None
    citations: list[ChatCitation] = field(default_factory=list)
    model: str = ""

    def to_payload(self) -> dict[str, object]:
        """The wire form; the same dict the JSON transport variant returns."""
        return {
            "session_id": self.session_id,
            "backend": self.backend,
            "num_turns": self.num_turns,
            "duration_ms": self.duration_ms,
            "is_error": self.is_error,
            "terminal_reason": self.terminal_reason,
            "total_cost_usd": self.total_cost_usd,
            "model_usage": self.model_usage,
            "model": self.model,
            "citations": [citation.to_payload() for citation in self.citations],
        }


ChatEvent = ChatDelta | ChatActivity | ChatWarning | ChatEnvelope
