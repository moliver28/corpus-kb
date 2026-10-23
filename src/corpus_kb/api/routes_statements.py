"""Statement-atom routes (Living Corpus W2).

Two thin adapters, one per catalogue row: POST /api/statements/{list,
timeline}. Each route parses JSON, calls the shared handler, and
returns JSONResponse through ``@route_errors`` — ONE error path. Handler
imports stay inside the route bodies so tests can monkeypatch them.
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
async def statements_list_route(request: Request) -> JSONResponse:
    """POST /api/statements/list — newest-first page of statement atoms."""
    from corpus_kb.tools.statements_tools import statements_list

    body = await parse_body(request)
    result = await statements_list(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        template_id=_opt_str(body, "template_id"),
        document_id=_opt_str(body, "document_id"),
        limit=_opt_int(body, "limit", 50),
    )
    return JSONResponse(result)


@route_errors
async def statements_timeline_route(request: Request) -> JSONResponse:
    """POST /api/statements/timeline — chronological belief history."""
    from corpus_kb.tools.statements_tools import statements_timeline

    body = await parse_body(request)
    result = await statements_timeline(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        text_query=_opt_str(body, "text_query"),
        limit=_opt_int(body, "limit", 200),
    )
    return JSONResponse(result)
