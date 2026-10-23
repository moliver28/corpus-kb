"""Conflict-op routes (Living Corpus W3).

Three thin adapters, one per catalogue row: POST /api/conflicts/{scan,
list, resolve}. Each route parses JSON, calls the shared handler, and
returns JSONResponse through ``@route_errors`` — ONE error path.
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
        verdict=_opt_str(body, "verdict"),
        limit=_opt_int(body, "limit", 50),
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
