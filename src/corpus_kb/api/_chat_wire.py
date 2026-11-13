"""Chat SSE wire (Phase-2 Wave 7): one framer for the five frame kinds.

Reuses ``ChunkFramer`` (api/_v1_wire.py) for the ``chat.completion.chunk``
content frames the SPA's parser already knows, and adds the house frames
keyed by an ``object`` discriminator: ``chat.activity``, ``chat.warning``,
``chat.envelope`` (the turn receipt), ``chat.error`` (the taxonomy
triple). ``: keep-alive`` comments every 15 s keep agent-minute turns
alive; ``data: [DONE]`` terminates every stream — never a half-open one.
"""

from __future__ import annotations

import json

from corpus_kb.api._v1_wire import SSE_MEDIA_TYPE, ChunkFramer

__all__ = ["SSE_MEDIA_TYPE", "ChatFramer"]

_KEEP_ALIVE = ": keep-alive\n\n"
_KEEP_ALIVE_S = 15.0


class ChatFramer:
    """One stream's frame factory over the ask wire's content frames."""

    def __init__(self, model: str) -> None:
        self._content = ChunkFramer(model)

    @property
    def keep_alive_s(self) -> float:
        """The idle gap between keep-alive comments."""
        return _KEEP_ALIVE_S

    def content(self, text: str) -> str:
        """One grounded text piece as a ``chat.completion.chunk`` frame."""
        return self._content.content(text)

    def activity(self, tool: str, summary: str, chunk_count: int | None) -> str:
        """One tool-progress line."""
        return self.house(
            {
                "object": "chat.activity",
                "tool": tool,
                "summary": summary,
                "chunk_count": chunk_count,
            }
        )

    def warning(self, text: str) -> str:
        """One CLI warning line, verbatim."""
        return self.house({"object": "chat.warning", "text": text})

    def envelope(self, payload: dict[str, object]) -> str:
        """The turn receipt: the same payload the JSON variant returns."""
        return self.house({"object": "chat.envelope", "envelope": payload})

    def error(self, status: int, message: str, error_type: str) -> str:
        """The taxonomy triple — the ONE mid-stream error shape."""
        return self.house(
            {
                "object": "chat.error",
                "status": status,
                "error": message,
                "error_type": error_type,
            }
        )

    def house(self, payload: dict[str, object]) -> str:
        """One house frame: ``data: {json}`` keyed by ``object``."""
        return f"data: {json.dumps(payload)}\n\n"

    def keep_alive(self) -> str:
        """One SSE comment line; the parser skips non-``data:`` lines."""
        return _KEEP_ALIVE

    def done(self) -> str:
        """The stream terminator."""
        return self._content.done()
