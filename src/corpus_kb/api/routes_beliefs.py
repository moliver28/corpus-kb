"""Belief-op routes (Living Corpus W4).

Four thin adapters, one per catalogue row: POST /api/beliefs/{current,
timeline, predict, compare}. Each route parses JSON, calls the shared
handler, and returns JSONResponse through ``@route_errors`` — ONE error
path.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_bool, opt_int, opt_str, parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


async def _pool() -> Any:
    from corpus_kb.api.research_http import research_pool

    return await research_pool()


@route_errors
async def beliefs_current_route(request: Request) -> JSONResponse:
    """POST /api/beliefs/current — current posteriors."""
    from corpus_kb.tools.beliefs_tools import beliefs_current

    body = await parse_body(request)
    result = await beliefs_current(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        subject=opt_str(body.get("subject")),
        limit=opt_int(body.get("limit"), 50, name="limit"),
        rebuild=opt_bool(body.get("rebuild"), True),
    )
    return JSONResponse(result)


@route_errors
async def beliefs_timeline_route(request: Request) -> JSONResponse:
    """POST /api/beliefs/timeline — era history for one belief."""
    from corpus_kb.tools.beliefs_tools import beliefs_timeline

    body = await parse_body(request)
    result = await beliefs_timeline(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        subject=opt_str(body.get("subject")),
        relation=opt_str(body.get("relation")),
        object=opt_str(body.get("object")),
    )
    return JSONResponse(result)


@route_errors
async def beliefs_predict_route(request: Request) -> JSONResponse:
    """POST /api/beliefs/predict — posterior predictive for one belief."""
    from corpus_kb.tools.beliefs_tools import beliefs_predict

    body = await parse_body(request)
    result = await beliefs_predict(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        subject=opt_str(body.get("subject")),
        relation=opt_str(body.get("relation")),
        object=opt_str(body.get("object")),
    )
    return JSONResponse(result)


@route_errors
async def beliefs_compare_route(request: Request) -> JSONResponse:
    """POST /api/beliefs/compare — one subject across objects."""
    from corpus_kb.tools.beliefs_tools import beliefs_compare

    body = await parse_body(request)
    result = await beliefs_compare(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        subject=opt_str(body.get("subject")),
    )
    return JSONResponse(result)
