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
  * the SSE chunk generator (valid ``chat.completion.chunk`` frames for
    the COMPLETE answer, then ``data: [DONE]`` — no token streaming yet).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

_DONE = "[DONE]"
_SSE_MEDIA_TYPE = "text/event-stream"
_NO_EVIDENCE_CONTENT = "No evidence matched this question in the knowledge base."


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
# SSE frames — honest streaming of the COMPLETE answer
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


async def sse_frames(content: str, model: str) -> AsyncIterator[str]:
    """Valid OpenAI chunk frames for the COMPLETE answer, then ``[DONE]``.

    Honest streaming: every frame is emitted only after generation ended.
    """
    completion_id = f"chatcmpl-{uuid4()}"
    created = int(time.time())

    def frame(delta: dict[str, Any], finish_reason: str | None) -> str:
        body = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(body)}\n\n"

    async for piece in split_pieces(content):
        yield frame({"content": piece}, None)
    yield frame({}, "stop")
    yield f"data: {_DONE}\n\n"
