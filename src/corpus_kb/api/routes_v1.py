"""OpenAI-compatible /v1 adapter (UX program Wave 2) — Starlette routes.

Three routes so any OpenAI SDK client or chat frontend (Open WebUI,
LibreChat, Jan) can use corpus-kb as its RAG backend:

  POST /v1/chat/completions  JSON + SSE; the ask operation behind it
  POST /v1/embeddings        the config-wired embedder (no database needed)
  GET  /v1/models            the embedding and answer model names

Gated by ``server.openai_compat`` (default true): ``v1_routes()`` reads
the config at app-build time and returns an empty tuple when the flag is
off, so the routes are simply never registered (no 404 handler needed).

Documented protocol deviations from the OpenAI wire format (see also
``api/_v1_wire.py``, the wire plumbing this module builds on):
  * Errors use the OpenAI error object ``{"error": {"message", "type",
    "code"}}`` — NOT the house ``{status, error, error_type}`` envelope
    (api/_body.py). These routes therefore bypass ``@route_errors``.
  * ``usage`` is omitted everywhere: token counts are not tracked yet.
  * SSE streaming is honest: the ask answer is generated in full first,
    then emitted as small delta frames. No token-level streaming exists.
  * A ``degraded`` ask envelope (generation unavailable) answers 503 —
    the retryable-service shape — with the note as the error message.
  * ``temperature`` is accepted but ignored: generation runs at
    temperature 0 (the ask operation's groundedness contract).
"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from corpus_kb.api._v1_wire import (
    ChatCompletionRequest,
    EmbeddingsRequest,
    completion_content,
    json_body,
    last_user_query,
    openai_error,
    sse_frames,
    validation_error,
)
from corpus_kb.config import load_config

_SSE_MEDIA_TYPE = "text/event-stream"

# ---------------------------------------------------------------------------
# Route handlers (tool imports INSIDE the bodies — the house test seam)
# ---------------------------------------------------------------------------


async def chat_completions(request: Request) -> Response:
    """POST /v1/chat/completions — the ask operation in OpenAI clothing."""
    from corpus_kb.tools.ask_tools import ask as ask_tool

    data, error = await json_body(request)
    if error is not None:
        return error
    try:
        payload = ChatCompletionRequest.model_validate(data)
    except ValidationError as exc:
        return validation_error(exc)
    query = last_user_query(payload.messages)
    if not query:
        return openai_error(
            400,
            "messages must contain a user message with non-empty content",
            "invalid_request_error",
            None,
        )
    cfg = load_config()
    llm_section = cfg.get("llm")
    default_model = str(llm_section.get("model", "")) if isinstance(llm_section, dict) else ""
    model = payload.model or default_model
    try:
        envelope = await ask_tool(query, config=cfg)
    except Exception as exc:  # ask never raises by contract; belt and braces
        return openai_error(500, f"internal error: {exc}", "internal_error", "internal_error")
    if not isinstance(envelope, dict):
        return openai_error(
            500, "internal error: bad ask envelope", "internal_error", "internal_error"
        )
    result = envelope.get("result")
    if envelope.get("status") == "degraded":
        note = "generation unavailable"
        if isinstance(result, dict):
            note = str(result.get("note", "") or note)
        return openai_error(503, note, "service_unavailable", "service_unavailable")
    content = completion_content(envelope)
    if payload.stream:
        return StreamingResponse(
            sse_frames(content, model),
            media_type=_SSE_MEDIA_TYPE,
            headers={"Cache-Control": "no-cache"},
        )
    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }
    )


async def embeddings(request: Request) -> Response:
    """POST /v1/embeddings — the config-wired embedder, no pool required."""
    from corpus_kb.rag import PgmlEmbedder, create_embedder

    data, error = await json_body(request)
    if error is not None:
        return error
    try:
        payload = EmbeddingsRequest.model_validate(data)
    except ValidationError as exc:
        return validation_error(exc)
    texts = [payload.input] if isinstance(payload.input, str) else list(payload.input)
    if not texts:
        return openai_error(
            400, "input must contain at least one string", "invalid_request_error", None
        )
    cfg = load_config()
    try:
        embedder = create_embedder(cfg, None)
        if isinstance(embedder, PgmlEmbedder):
            vectors = await embedder.embed_batch(texts)
        else:
            # Blocking Ollama round trip stays off the event loop.
            vectors = await asyncio.to_thread(embedder.embed_batch, texts)
    except Exception as exc:
        return openai_error(500, f"internal error: {exc}", "internal_error", "internal_error")
    embedding_section = cfg.get("embedding")
    default_model = (
        str(embedding_section.get("model", "")) if isinstance(embedding_section, dict) else ""
    )
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {"object": "embedding", "index": index, "embedding": vector}
                for index, vector in enumerate(vectors)
            ],
            "model": payload.model or default_model,
        }
    )


async def models(request: Request) -> Response:
    """GET /v1/models — the embedding model plus the answer model."""
    cfg = load_config()
    embedding_section = cfg.get("embedding")
    llm_section = cfg.get("llm")
    embedding_model = (
        str(embedding_section.get("model", "")) if isinstance(embedding_section, dict) else ""
    )
    llm_model = str(llm_section.get("model", "")) if isinstance(llm_section, dict) else ""
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {"id": embedding_model, "object": "model"},
                {"id": llm_model, "object": "model"},
            ],
        }
    )


def v1_routes() -> tuple[Route, ...]:
    """The /v1 routes; empty when ``server.openai_compat`` is off."""
    cfg = load_config()
    server = cfg.get("server")
    if isinstance(server, dict) and not server.get("openai_compat", True):
        return ()
    return (
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/embeddings", embeddings, methods=["POST"]),
        Route("/v1/models", models, methods=["GET"]),
    )
