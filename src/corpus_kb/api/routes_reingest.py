"""The #202 bulk re-embed route (kept out of routes_ingest.py for the
250-line rule).

POST /api/ingest/reingest

Thin adapter: parse JSON, call the shared tool layer, return
JSONResponse. The route runs through ``@route_errors`` (api._body) —
ONE error path, so a typed ConfigError (width mismatch) is the standard
error envelope, never a bare 500.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import DEFAULT_TENANT_ID, opt_bool, opt_int, parse_body, route_errors


@route_errors
async def ingest_reingest_route(request: Request) -> JSONResponse:
    """POST /api/ingest/reingest — bulk re-embed (honest receipt)."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_reingest import DEFAULT_BATCH_SIZE, ingest_reingest

    body = await parse_body(request)
    result = await ingest_reingest(
        await research_pool(),
        tenant_id=body.get("tenant_id", DEFAULT_TENANT_ID),
        project_id=body.get("project_id"),
        dry_run=opt_bool(body.get("dry_run"), False),
        batch_size=opt_int(body.get("batch_size"), DEFAULT_BATCH_SIZE, name="batch_size"),
    )
    # Same contract as ingest-status/reembed: a pre-migration database is
    # 503, never a silent 200.
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)
