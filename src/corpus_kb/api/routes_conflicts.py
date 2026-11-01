"""Conflict-op routes (Living Corpus W3).

Three thin adapters, one per catalogue row: POST /api/conflicts/{scan,
list, resolve}. Each route parses JSON, calls the shared handler, and
returns JSONResponse through ``@route_errors`` — ONE error path.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_int, opt_str, parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


async def _pool() -> Any:
    from corpus_kb.api.research_http import research_pool

    return await research_pool()


@route_errors
async def conflicts_scan_route(request: Request) -> JSONResponse:
    """POST /api/conflicts/scan — run the contradiction cascade."""
    from corpus_kb.tools.conflicts_tools import conflicts_scan

    body = await parse_body(request)
    result = await conflicts_scan(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
    )
    return JSONResponse(result)


@route_errors
async def conflicts_list_route(request: Request) -> JSONResponse:
    """POST /api/conflicts/list — newest-first page of conflict flags."""
    from corpus_kb.tools.conflicts_tools import conflicts_list

    body = await parse_body(request)
    result = await conflicts_list(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        verdict=opt_str(body.get("verdict")),
        limit=opt_int(body.get("limit"), 50, name="limit"),
    )
    return JSONResponse(result)


@route_errors
async def conflicts_resolve_route(request: Request) -> JSONResponse:
    """POST /api/conflicts/resolve — finalize one conflict flag."""
    from corpus_kb.tools.conflicts_tools import conflicts_resolve

    body = await parse_body(request)
    result = await conflicts_resolve(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        conflict_id=str(body.get("conflict_id", "")),
        resolved_verdict=str(body.get("resolved_verdict", "")),
        resolved_by=str(body.get("resolved_by", "")),
    )
    return JSONResponse(result)
