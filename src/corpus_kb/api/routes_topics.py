"""Topics route family (Wave 4 + analysis program trends op).

POST /api/topics/build
GET  /api/topics
POST /api/topics/trends

Thin adapters: parse JSON, call the shared tool layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path, so a missing ``[topics]`` extra answers 503 with
``error_type: dependency_missing``. Handler imports stay inside the
route bodies so tests can monkeypatch the tool-module functions.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_int, parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


@route_errors
async def topics_build_route(request: Request) -> JSONResponse:
    """POST /api/topics/build — cluster stored embeddings into topics."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import topics_tools

    body = await parse_body(request)
    result = await topics_tools.topics_build(
        await research_pool(),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
    )
    # A pre-migration database is 503, never a silent 200; a disabled
    # feature is the documented dormant default and answers 200 with the
    # honest no-op envelope.
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def topics_list_route(request: Request) -> JSONResponse:
    """GET /api/topics — the built topics, largest first."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import topics_tools

    result = await topics_tools.topics_list(
        await research_pool(),
        tenant_id=request.query_params.get("tenant_id", _TENANT_DEFAULT),
        limit=opt_int(
            request.query_params.get("limit"), topics_tools.DEFAULT_LIST_LIMIT, name="limit"
        ),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def topics_trends_route(request: Request) -> JSONResponse:
    """POST /api/topics/trends — per-topic series over binned document dates."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import topics_tools

    body = await parse_body(request)
    bins = body.get("bins")
    result = await topics_tools.topics_trends(
        await research_pool(),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
        bins=opt_int(bins, None, name="bins"),
    )
    # A pre-migration database is 503, never a silent 200; a disabled
    # feature is the documented dormant default and answers 200 with the
    # honest no-op envelope.
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)
