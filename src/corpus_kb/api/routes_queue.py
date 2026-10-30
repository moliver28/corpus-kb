"""Queue-op routes (textbook-ingestion program, Wave 1).

Three thin adapters, one per catalogue row: POST /api/queue/{enqueue,
status, cancel}. Each route parses JSON, calls the shared handler, and
returns JSONResponse through ``@route_errors`` — ONE error path.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_int, opt_str, parse_body, route_errors

_TENANT_DEFAULT = "00000000-0000-0000-0000-000000000001"


async def _pool() -> Any:
    from corpus_kb.api.research_http import research_pool

    return await research_pool()


@route_errors
async def queue_enqueue_route(request: Request) -> JSONResponse:
    """POST /api/queue/enqueue — idempotent enqueue by content hash."""
    from corpus_kb.tools.queue_tools import queue_enqueue

    body = await parse_body(request)
    result = await queue_enqueue(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        path=opt_str(body.get("path")) or "",
        title=opt_str(body.get("title")),
        profile=opt_str(body.get("profile")),
        strategy_config=body.get("strategy_config"),
        project_id=opt_str(body.get("project_id")),
    )
    return JSONResponse(result)


@route_errors
async def queue_status_route(request: Request) -> JSONResponse:
    """POST /api/queue/status — one row or the active list."""
    from corpus_kb.tools.queue_tools import queue_status

    body = await parse_body(request)
    result = await queue_status(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        queue_id=opt_str(body.get("queue_id")),
        limit=opt_int(body.get("limit"), 50),
    )
    return JSONResponse(result)


@route_errors
async def queue_cancel_route(request: Request) -> JSONResponse:
    """POST /api/queue/cancel — cancel one pending or running row."""
    from corpus_kb.tools.queue_tools import queue_cancel

    body = await parse_body(request)
    result = await queue_cancel(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        queue_id=opt_str(body.get("queue_id")) or "",
    )
    return JSONResponse(result)
