"""Event-store health route (issue #350).

GET /api/events/summary — the ONE event-store health read: counts by
topic, latest notification id, projection checkpoints, dead letters,
and the recent ingest runs. Thin adapter over the shared
``tools.events_tools.events_summary`` function (the statements-routes
shape), so MCP/socket/CLI answer the same envelope.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID


@route_errors
async def events_summary_route(request: Request) -> JSONResponse:
    """GET /api/events/summary — event-store health for one tenant."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.events_tools import events_summary

    tenant_id = request.query_params.get("tenant_id", DEFAULT_TENANT_ID)
    result = await events_summary(await research_pool(), tenant_id=tenant_id)
    return JSONResponse(result)
