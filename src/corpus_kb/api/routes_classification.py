"""Classification route family (analysis program Wave 0).

POST /api/classification/build
GET  /api/classification

Thin adapters: parse JSON, call the shared tool layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests
can monkeypatch the tool-module functions. A disabled feature answers
200 with the honest no-op envelope (the documented dormant default).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_int, parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


@route_errors
async def classification_build_route(request: Request) -> JSONResponse:
    """POST /api/classification/build — fit the classifier, write predictions."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import classification_tools

    body = await parse_body(request)
    seed_file = body.get("seed_file")
    result = await classification_tools.classification_build(
        await research_pool(),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
        seed_file=str(seed_file) if seed_file is not None else None,
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def classification_list_route(request: Request) -> JSONResponse:
    """GET /api/classification — the built predictions, bounded window."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import classification_tools

    result = await classification_tools.classification_list(
        await research_pool(),
        tenant_id=request.query_params.get("tenant_id", _TENANT_DEFAULT),
        limit=opt_int(
            request.query_params.get("limit"), classification_tools.DEFAULT_LIST_LIMIT, name="limit"
        ),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)
