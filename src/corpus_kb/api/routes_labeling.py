"""Labeling route family (analysis program Wave 0).

POST /api/labeling/build
GET  /api/labeling
GET  /api/labeling/lfs
POST /api/labeling/triage      — the U39 review queue (read-only op)
POST /api/labeling/gold-check  — the F6 gold-set validation (read-only op)

Thin adapters: parse JSON, call the shared tool layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests
can monkeypatch the tool-module functions. A disabled feature answers
200 with the honest no-op envelope (the documented dormant default).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_float, opt_int, parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


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
        limit=opt_int(
            request.query_params.get("limit"), labeling_tools.DEFAULT_LIST_LIMIT, name="limit"
        ),
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
        limit=opt_int(
            request.query_params.get("limit"), labeling_tools.DEFAULT_LIST_LIMIT, name="limit"
        ),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def labeling_triage_route(request: Request) -> JSONResponse:
    """POST /api/labeling/triage — the weak-label review queue (U39)."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import labeling_triage_ops, labeling_triage_tools

    body = await parse_body(request)
    result = await labeling_triage_tools.labeling_triage(
        await research_pool(),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
        cv_folds=opt_int(
            body.get("cv_folds"), labeling_triage_ops.DEFAULT_TRIAGE_FOLDS, name="cv_folds"
        ),
        flag_threshold=opt_float(
            body.get("flag_threshold"),
            labeling_triage_ops.DEFAULT_FLAG_THRESHOLD,
            name="flag_threshold",
        ),
        limit=opt_int(body.get("limit"), labeling_triage_ops.DEFAULT_QUEUE_LIMIT, name="limit"),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def labeling_gold_check_route(request: Request) -> JSONResponse:
    """POST /api/labeling/gold-check — the gold-set validation (F6)."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import labeling_gold_tools

    body = await parse_body(request)
    result = await labeling_gold_tools.labeling_gold_check(
        await research_pool(),
        gold_file=str(body.get("gold_file", "")),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
        confidence_level=opt_float(body.get("confidence_level"), 0.95, name="confidence_level"),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)
