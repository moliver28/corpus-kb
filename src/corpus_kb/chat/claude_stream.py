"""Claude stream mapping: SDK messages to chat events, one turn.

The adapter closes over the session's stderr collector; the CLI's stderr
lines drain verbatim as ``ChatWarning`` events before the receipt, so
expiry and org-policy notices surface with the turn they belong to
(research Q2/Q3). Interrupt does not clear the SDK buffer: after an
interrupt the pending ``ResultMessage`` still arrives and the receipt
still emits — an honest stop record ([D7]).

Duck-typed on purpose: the tests drive the mapping with plain namespace
fakes, and the real SDK dataclasses carry the same attribute names.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from corpus_kb.chat.agent_tools import citations_from_contract
from corpus_kb.chat.events import (
    ChatActivity,
    ChatCitation,
    ChatDelta,
    ChatEnvelope,
    ChatEvent,
    ChatWarning,
)
from corpus_kb.chat.session import AgentClient, TurnStream
from corpus_kb.errors import InternalError

__all__ = ["turn_stream"]


def turn_stream(stderr_lines: list[str]) -> TurnStream:
    """The message-to-event adapter for one session's turns."""

    async def turn(
        client: AgentClient,
        text: str,
        history: list[dict[str, str]],
        k: int | None,
    ) -> AsyncIterator[ChatEvent]:
        from claude_agent_sdk import CLIConnectionError, ProcessError

        citations: list[ChatCitation] = []
        pending_envelope: ChatEnvelope | None = None
        try:
            await client.query(text)
            async for message in client.receive_response():
                for event in _message_events(message, citations):
                    if isinstance(event, ChatEnvelope):
                        pending_envelope = event
                    else:
                        yield event
        except (CLIConnectionError, ProcessError) as exc:
            raise InternalError(str(exc)) from exc
        for line in stderr_lines:
            yield ChatWarning(line)
        stderr_lines.clear()
        if pending_envelope is not None:
            yield pending_envelope

    return turn


def _message_events(message: object, citations: list[ChatCitation]) -> list[ChatEvent]:
    """Map one SDK message (or fake look-alike) onto zero or more events."""
    events: list[ChatEvent] = []
    raw_event = getattr(message, "event", None)
    if isinstance(raw_event, dict):
        delta = _delta_of(raw_event)
        if delta is not None:
            events.append(delta)
        return events
    num_turns = getattr(message, "num_turns", None)
    if num_turns is not None and not hasattr(message, "content"):
        events.append(_envelope_of(message, citations))
        return events
    for block in getattr(message, "content", None) or []:
        name = getattr(block, "name", None)
        if name is not None and hasattr(block, "input"):
            if name == "mcp__corpus__search":
                events.append(ChatActivity("search", "Searching the corpus...", None))
            continue
        tool_use_id = getattr(block, "tool_use_id", None)
        if tool_use_id is not None:
            activity = _result_activity(block, citations)
            if activity is not None:
                events.append(activity)
    return events


def _delta_of(raw_event: dict[str, object]) -> ChatDelta | None:
    """One ``content_block_delta`` text piece, or None."""
    if raw_event.get("type") != "content_block_delta":
        return None
    delta = raw_event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return None
    text = delta.get("text")
    return ChatDelta(str(text)) if text else None


def _result_text(content: object) -> str:
    """The tool result's text: a plain string or a list of text blocks.

    The SDK types ``ToolResultBlock.content`` as ``str | list[dict] |
    None`` and passes the CLI's blocks through verbatim; the in-process
    SDK-MCP tool returns the list form.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _result_activity(block: object, citations: list[ChatCitation]) -> ChatActivity | None:
    """The activity line from a corpus tool result ("Read N chunks")."""
    try:
        contract = json.loads(_result_text(getattr(block, "content", None)))
    except ValueError:
        return None
    if not isinstance(contract, dict):
        return None
    rows = contract.get("results")
    count = len(rows) if isinstance(rows, list) else 0
    citations.extend(citations_from_contract(contract))
    return ChatActivity("search", f"Read {count} chunks", count)


def _envelope_of(message: object, citations: list[ChatCitation]) -> ChatEnvelope:
    """The turn receipt from the terminal ``ResultMessage``."""
    model_usage_raw = getattr(message, "model_usage", None)
    model_usage: dict[str, object] | None = (
        dict(model_usage_raw) if isinstance(model_usage_raw, dict) else None
    )
    model = next(iter(model_usage), "") if model_usage else ""
    return ChatEnvelope(
        session_id=str(getattr(message, "session_id", "") or ""),
        backend="claude-agent-sdk",
        num_turns=int(getattr(message, "num_turns", 0) or 0),
        duration_ms=int(getattr(message, "duration_ms", 0) or 0),
        is_error=bool(getattr(message, "is_error", False)),
        terminal_reason=getattr(message, "terminal_reason", None),
        total_cost_usd=getattr(message, "total_cost_usd", None),
        model_usage=model_usage,
        model=str(model),
        citations=citations,
    )
