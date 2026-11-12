"""Ask route (UX program Wave 1): POST /api/ask.

Thin adapter, api/routes_search.py style: parse JSON, call the shared
tool layer (``tools/ask_tools.ask`` — the ONE function every surface
routes through), return JSONResponse. The route runs through
``@route_errors`` (api._body) — ONE error path. The tool import stays
inside the route body so tests can monkeypatch the tool module.

``stream: true`` in the body switches the TRANSPORT, not the operation
(issue #131): the same ``ask_stream`` delta events render as SSE for the
SPA — ``chat.completion.chunk`` content frames as grounded sentences
complete, then one house ``ask.envelope`` frame carrying the COMPLETE ask
envelope, then ``[DONE]``. The JSON envelope on this surface is unchanged
(the parity rule: streaming is a transport variant). The SPA keeps the
envelope view (citations, verify, degraded evidence) it needs at settle
time — the /v1 OpenAI wire cannot carry those, by its own wire-clean
contract.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from corpus_kb.api._body import (
    DEFAULT_TENANT_ID,
    error_response,
    opt_int,
    parse_body,
    route_errors,
)
from corpus_kb.api._v1_wire import SSE_MEDIA_TYPE, ChunkFramer


@route_errors
async def ask(request: Request) -> Response:
    """POST /api/ask — grounded answer with sentence-level citations."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ask_tools import ask as ask_tool
    from corpus_kb.tools.ask_tools import ask_stream as ask_stream_tool

    body = await parse_body(request)
    query = str(body.get("query", "")).strip()
    if not query:
        return error_response("validation_error", "query is required")
    tenant_id = str(body.get("tenant_id", DEFAULT_TENANT_ID))
    k = opt_int(body.get("k"), None, name="k")
    if bool(body.get("stream", False)):
        from corpus_kb.config import load_config

        llm_section = load_config().get("llm")
        model = str(llm_section.get("model", "")) if isinstance(llm_section, dict) else ""
        events = ask_stream_tool(query, tenant_id=tenant_id, k=k, pg_pool=await research_pool())
        return StreamingResponse(
            _sse_events(events, model),
            media_type=SSE_MEDIA_TYPE,
            headers={"Cache-Control": "no-cache"},
        )
    result = await ask_tool(query, tenant_id=tenant_id, k=k, pg_pool=await research_pool())
    return JSONResponse(result)


async def _sse_events(events: AsyncGenerator[Any, None], model: str) -> AsyncIterator[str]:
    """SSE body for /api/ask: chunk frames, then the envelope frame.

    The envelope frame is the honest settle receipt: the SAME payload the
    non-streaming surface returns, so the SPA renders its exact settled
    view (citations, verify badge, degraded evidence) after streaming.
    """
    from corpus_kb.ask.streaming import AskStreamDelta, AskStreamFinal

    framer = ChunkFramer(model)
    try:
        event: Any = await anext(events, None)
        while event is not None:
            if isinstance(event, AskStreamDelta):
                yield framer.content(event.text)
            elif isinstance(event, AskStreamFinal):
                yield framer.raw(
                    {
                        "id": framer.completion_id,
                        "object": "ask.envelope",
                        "created": framer.created,
                        "model": framer.model,
                        "envelope": event.bundle.to_payload(),
                    }
                )
                yield framer.stop()
            event = await anext(events, None)
    except Exception as exc:  # the seam never raises by contract; belt and braces
        yield framer.raw({"object": "ask.error", "message": f"internal error: {exc}"})
    finally:
        # A client disconnect must still close the seam (its pool with it).
        await events.aclose()
    yield framer.done()
