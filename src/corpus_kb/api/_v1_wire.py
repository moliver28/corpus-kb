"""OpenAI /v1 wire plumbing — request models, errors, SSE frames.

The protocol-facing half of the /v1 adapter (routes live in
``api/routes_v1.py``). One definition for the OpenAI wire pieces the
three routes share:

  * pydantic request models (pydantic is used ONLY for wire validation);
  * the OpenAI error object ``{"error": {"message", "type", "code"}}`` —
    a DOCUMENTED deviation from the house ``{status, error, error_type}``
    envelope (api/_body.py); these routes bypass ``@route_errors``;
  * the ask-envelope -> content mapping (answer + portable ``Sources:``
    block; honest no-evidence sentence when retrieval found nothing);
  * the SSE machinery (``ChunkFramer`` + ``sse_body``): valid
    ``chat.completion.chunk`` frames streamed from the ask operation's
    delta events (issue #131), then ``data: [DONE]``. Content frames
    carry grounded sentences as they complete; the Sources block lands
    with the envelope; degraded-after-content emits the OpenAI error
    frame and stops — never a fake completion.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

_DONE = "[DONE]"
_NO_EVIDENCE_CONTENT = "No evidence matched this question in the knowledge base."
SSE_MEDIA_TYPE = "text/event-stream"


# ---------------------------------------------------------------------------
# Request models (pydantic is used ONLY for wire request validation)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """One OpenAI chat message; only the text content is consumed."""

    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """POST /v1/chat/completions request body (the consumed subset)."""

    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None


class EmbeddingsRequest(BaseModel):
    """POST /v1/embeddings request body (the consumed subset)."""

    input: str | list[str]
    model: str | None = None


# ---------------------------------------------------------------------------
# Error mapping — the OpenAI error object (documented deviation)
# ---------------------------------------------------------------------------


def openai_error(status: int, message: str, err_type: str, code: str | None) -> JSONResponse:
    """The OpenAI wire error: ``{"error": {"message", "type", "code"}}``."""
    error: dict[str, Any] = {"message": message, "type": err_type, "code": code}
    return JSONResponse({"error": error}, status_code=status)


async def json_body(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Parse the JSON body; the second element is the error response, if any."""
    try:
        data = await request.json()
    except Exception:
        return None, openai_error(
            400, "request body must be valid JSON", "invalid_request_error", None
        )
    if not isinstance(data, dict):
        return None, openai_error(
            400, "request body must be a JSON object", "invalid_request_error", None
        )
    return data, None


def validation_error(exc: ValidationError) -> JSONResponse:
    """Map a pydantic failure onto the 422 OpenAI error shape."""
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return openai_error(422, f"invalid request body: {details}", "invalid_request_error", None)


# ---------------------------------------------------------------------------
# Chat completion content mapping (ask envelope -> OpenAI wire)
# ---------------------------------------------------------------------------


def last_user_query(messages: list[ChatMessage]) -> str:
    """The LAST user message's content (the OpenAI convention for a query)."""
    for message in reversed(messages):
        if message.role == "user":
            return message.content.strip()
    return ""


def sources_block(sources: object) -> str:
    """Markdown ``Sources:`` list appended to the answer (portable citation)."""
    if not isinstance(sources, list) or not sources:
        return ""
    lines = ["", "", "Sources:"]
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            continue
        name = str(source.get("source", ""))
        chunk_index = source.get("chunk_index")
        suffix = f" — chunk {chunk_index}" if isinstance(chunk_index, int) else ""
        lines.append(f"{index}. {name}{suffix}")
    return "\n".join(lines)


def completion_content(envelope: dict[str, Any]) -> str:
    """The assistant content for a success or empty ask envelope.

    ``success`` ships the answer plus the Sources block; ``empty`` ships
    one honest no-evidence sentence. Nothing is appended about grounding —
    the verify verdicts stay inside corpus-kb, keeping the wire clean.
    """
    if envelope.get("status") == "empty":
        return _NO_EVIDENCE_CONTENT
    result = envelope.get("result")
    if not isinstance(result, dict):
        return ""
    answer = str(result.get("answer", ""))
    return answer + sources_block(result.get("sources"))


# ---------------------------------------------------------------------------
# SSE machinery — delta frames over the ask operation's stream events
# ---------------------------------------------------------------------------


async def split_pieces(text: str, limit: int = 40) -> AsyncIterator[str]:
    """Yield ``text`` as word-boundary pieces whose join equals ``text``."""
    piece = ""
    for word in text.split(" "):
        candidate = f"{piece} {word}" if piece else word
        if len(candidate) > limit and piece:
            yield f"{piece} "
            piece = word
        else:
            piece = candidate
    if piece:
        yield piece


class ChunkFramer:
    """One stream's ``chat.completion.chunk`` frame factory.

    Stable per-stream ``id`` and ``created`` (one completion, one identity),
    deterministic frame text. ``raw()`` lets a house surface add one
    non-OpenAI frame (the /api/ask envelope frame) on the same SSE wire.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.completion_id = f"chatcmpl-{uuid4()}"
        self.created = int(time.time())

    def frame(self, delta: dict[str, Any], finish_reason: str | None) -> str:
        body = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return self.raw(body)

    def raw(self, payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def content(self, text: str) -> str:
        return self.frame({"content": text}, None)

    def stop(self) -> str:
        return self.frame({}, "stop")

    def error(self, message: str, err_type: str, code: str | None) -> str:
        return self.raw({"error": {"message": message, "type": err_type, "code": code}})

    def done(self) -> str:
        return f"data: {_DONE}\n\n"


async def sse_body(events: AsyncGenerator[Any, None], first: Any, model: str) -> AsyncIterator[str]:
    """The /v1 SSE body over the ask stream events, then ``[DONE]``.

    ``first`` is the event the route already awaited (it decides between
    this stream and an error response before headers start). Content
    frames carry the grounded sentences as they complete; the Sources
    block lands once the final envelope is known. A degraded envelope
    after content frames emits the OpenAI error frame and stops — the
    mid-stream error convention: no fake completion, no ``[DONE]``.
    """
    from corpus_kb.ask.composer import STATUS_DEGRADED, STATUS_EMPTY, STATUS_SUCCESS
    from corpus_kb.ask.streaming import AskStreamDelta, AskStreamFinal

    framer = ChunkFramer(model)
    content_seen = False
    try:
        event: Any = first
        while event is not None:
            if isinstance(event, AskStreamDelta):
                content_seen = True
                yield framer.content(event.text)
            elif isinstance(event, AskStreamFinal):
                bundle = event.bundle
                if bundle.status == STATUS_SUCCESS:
                    async for piece in split_pieces(sources_block(bundle.sources)):
                        yield framer.content(piece)
                    yield framer.stop()
                elif bundle.status == STATUS_EMPTY:
                    yield framer.content(_NO_EVIDENCE_CONTENT)
                    yield framer.stop()
                elif bundle.status == STATUS_DEGRADED and content_seen:
                    note = str(bundle.note or "generation unavailable")
                    yield framer.error(note, "service_unavailable", "service_unavailable")
                    return
            event = await anext(events, None)
    except Exception as exc:  # the seam never raises by contract; belt and braces
        yield framer.error(f"internal error: {exc}", "internal_error", "internal_error")
        return
    finally:
        # A client disconnect must still close the seam (its pool with it).
        await events.aclose()
    yield framer.done()
