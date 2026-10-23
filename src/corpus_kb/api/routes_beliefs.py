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

from corpus_kb.api._body import parse_body, route_errors

_TENANT_DEFAULT = "00000000-0000-0000-0000-000000000001"


async def _pool() -> Any:
    from corpus_kb.api.research_http import research_pool

    return await research_pool()


def _opt_str(body: dict[str, Any], key: str) -> str | None:
    raw = body.get(key)
    return str(raw) if raw is not None else None


def _opt_int(body: dict[str, Any], key: str, default: int) -> int:
    raw = body.get(key)
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _opt_bool(body: dict[str, Any], key: str, default: bool) -> bool:
    raw = body.get(key)
    return default if raw is None else bool(raw)


@route_errors
async def beliefs_current_route(request: Request) -> JSONResponse:
    """POST /api/beliefs/current — current posteriors."""
    from corpus_kb.tools.beliefs_tools import beliefs_current

    body = await parse_body(request)
    result = await beliefs_current(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        subject=_opt_str(body, "subject"),
        limit=_opt_int(body, "limit", 50),
        rebuild=_opt_bool(body, "rebuild", True),
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
        subject=_opt_str(body, "subject"),
        relation=_opt_str(body, "relation"),
        object=_opt_str(body, "object"),
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
        subject=_opt_str(body, "subject"),
        relation=_opt_str(body, "relation"),
        object=_opt_str(body, "object"),
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
        subject=_opt_str(body, "subject"),
    )
    return JSONResponse(result)
