"""Chat routes (Phase-2 Wave 7): the streaming session surface.

NON-MANIFEST ([D2]): these routes ride the ``runtime-config.json``
sanction — config-gated (``chat.enabled``), never in the surface
catalogue, never an op envelope. Every route no-ops with a typed error
while chat is disabled — never a 404 drift. ``SERVER_AUTO_APPROVE`` is
untouched ([D13]): chat is not an MCP op.

The route layer awaits the manager's sweep before each create and get,
so an idle past-TTL session frees the cap honestly (the ``ChatManager.get``
contract). The SSE body lives in ``api/_chat_sse``; its disconnect close
rides the manager so a closed session deregisters.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import cast

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from corpus_kb.api._body import error_response, opt_int, parse_body, route_errors
from corpus_kb.api._chat_sse import chat_sse
from corpus_kb.api._chat_wire import SSE_MEDIA_TYPE, ChatFramer
from corpus_kb.chat.events import ChatEnvelope
from corpus_kb.chat.session import ChatManager, ChatSession
from corpus_kb.errors import InternalError, NotFoundError

__all__ = [
    "chat_status",
    "create_chat_session",
    "list_chat_sessions",
    "send_chat_message",
]


def _config() -> dict[str, object]:
    from corpus_kb.config import load_config

    return cast(dict[str, object], load_config())


async def _pool() -> asyncpg.Pool:
    from corpus_kb.api.research_http import research_pool

    return cast(asyncpg.Pool, await research_pool())


def _manager() -> ChatManager | None:
    import corpus_kb.chat as chat_core

    manager = chat_core.get_manager()
    return manager if isinstance(manager, ChatManager) else None


async def _session_or_error(session_id: str) -> ChatSession:
    """The live session, after the manager's sweep has run.

    Expired sessions still registered answer None; the sweep has already
    closed their clients, so the TTL is live and the cap slot is free.
    """
    manager = _manager()
    if manager is not None:
        await manager.sweep()
    session = manager.get(session_id) if manager is not None else None
    if session is None:
        raise NotFoundError(f"No live chat session {session_id}. Open one first.")
    return session


@route_errors
async def create_chat_session(request: Request) -> Response:
    """POST /api/chat/sessions — open one session, optionally resuming."""
    import corpus_kb.chat as chat_core

    body = await parse_body(request)
    resume_raw = body.get("resume")
    resume = resume_raw.strip() if isinstance(resume_raw, str) else ""
    config = _config()
    chat_core.enabled_rung(config)  # the typed refusals cost no pool work
    manager = _manager()
    if manager is not None:
        await manager.sweep()
    session = await chat_core.create_session(config, await _pool(), resume or None)
    return JSONResponse(
        {
            "status": "success",
            "result": {"session_id": session.session_id, "backend": session.backend},
        }
    )


@route_errors
async def send_chat_message(request: Request) -> Response:
    """POST /api/chat/sessions/{sid}/messages — one turn on one session.

    ``stream: true`` switches the TRANSPORT, not the operation (the ask
    parity rule): the same turn events render as SSE — content frames,
    then exactly one ``chat.envelope``, then ``[DONE]``. ``stream: false``
    returns the same receipt as JSON.
    """
    import corpus_kb.chat as chat_core

    config = _config()
    chat_core.enabled_rung(config)  # disabled answers typed, never a 404 drift
    session = await _session_or_error(request.path_params["sid"])
    manager = _manager()
    body = await parse_body(request)
    text = str(body.get("text", "")).strip()
    if not text:
        return error_response("validation_error", "text is required")
    k = opt_int(body.get("k"), None, name="k")
    events = session.send(text, k)
    if bool(body.get("stream", False)):
        return StreamingResponse(
            chat_sse(events, manager, session, ChatFramer("")),
            media_type=SSE_MEDIA_TYPE,
            headers={"Cache-Control": "no-cache"},
        )
    receipt = await _receipt_of(events)
    return JSONResponse({"status": "success", "result": receipt})


async def _receipt_of(events: AsyncGenerator[object, None]) -> dict[str, object]:
    """Consume one turn to its receipt (the JSON transport variant)."""
    try:
        async for event in events:
            if isinstance(event, ChatEnvelope):
                return event.to_payload()
    finally:
        await events.aclose()
    raise InternalError("The turn ended without a receipt.")


@route_errors
async def list_chat_sessions(request: Request) -> Response:
    """GET /api/chat/sessions — the CLI's own session store, newest first."""
    import corpus_kb.chat as chat_core
    from corpus_kb.chat.claude_rung import list_transcripts

    config = _config()
    chat_core.enabled_rung(config)  # disabled answers typed, never a 404 drift
    limit = opt_int(request.query_params.get("limit"), 50, name="limit")
    offset = opt_int(request.query_params.get("offset"), 0, name="offset")
    rows = await list_transcripts(config, limit, offset)
    return JSONResponse(
        {
            "status": "success",
            "result": {"sessions": rows, "limit": limit, "offset": offset},
        }
    )


@route_errors
async def chat_status(request: Request) -> Response:
    """GET /api/chat/status — the honest-state payload (step 4's rows)."""
    from corpus_kb._setup.doctor_chat import chat_checks
    from corpus_kb.chat.settings import read_chat_settings

    config = _config()
    settings = read_chat_settings(config)
    return JSONResponse(
        {
            "status": "success",
            "result": {
                "enabled": settings.enabled,
                "backend": settings.backend,
                "checks": [asdict(row) for row in chat_checks(config)],
            },
        }
    )
