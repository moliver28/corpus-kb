"""Analysis route family (analysis program Wave 0).

POST /api/analysis/backfill-dates
POST /api/analyze

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
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


@route_errors
async def analysis_backfill_dates_route(request: Request) -> JSONResponse:
    """POST /api/analysis/backfill-dates — backfill documents.document_date."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import analysis_tools

    body = await parse_body(request)
    result = await analysis_tools.analysis_backfill_dates(
        await research_pool(),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def analyze_run_route(request: Request) -> JSONResponse:
    """POST /api/analyze — run the composed corpus analysis."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import analysis_tools

    body = await parse_body(request)
    output = body.get("output")
    result = await analysis_tools.analyze_run(
        await research_pool(),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
        output=str(output) if output is not None else None,
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)
