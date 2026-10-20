"""Labeling route family (analysis program Wave 0).

POST /api/labeling/build
GET  /api/labeling
GET  /api/labeling/lfs

Thin adapters: parse JSON, call the shared tool layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests
can monkeypatch the tool-module functions. A disabled feature answers
200 with the honest no-op envelope (the documented dormant default).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import parse_body, route_errors

_TENANT_DEFAULT = "00000000-0000-0000-0000-000000000001"


@route_errors
async def labeling_build_route(request: Request) -> JSONResponse:
    """POST /api/labeling/build — score the labeling functions, write labels."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import labeling_tools

    body = await parse_body(request)
    result = await labeling_tools.labeling_build(
        await research_pool(),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def labeling_list_route(request: Request) -> JSONResponse:
    """GET /api/labeling — the built chunk labels, bounded window."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import labeling_tools

    result = await labeling_tools.labeling_list(
        await research_pool(),
        tenant_id=request.query_params.get("tenant_id", _TENANT_DEFAULT),
        limit=int(request.query_params.get("limit", labeling_tools.DEFAULT_LIST_LIMIT)),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def labeling_lfs_route(request: Request) -> JSONResponse:
    """GET /api/labeling/lfs — the registered labeling functions."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import labeling_tools

    result = await labeling_tools.labeling_lfs(
        await research_pool(),
        tenant_id=request.query_params.get("tenant_id", _TENANT_DEFAULT),
        limit=int(request.query_params.get("limit", labeling_tools.DEFAULT_LIST_LIMIT)),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)
