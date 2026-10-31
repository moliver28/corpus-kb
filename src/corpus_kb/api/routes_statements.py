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

from corpus_kb.api._body import opt_int, opt_str, parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


async def _pool() -> Any:
    from corpus_kb.api.research_http import research_pool

    return await research_pool()


@route_errors
async def statements_list_route(request: Request) -> JSONResponse:
    """POST /api/statements/list — newest-first page of statement atoms."""
    from corpus_kb.tools.statements_tools import statements_list

    body = await parse_body(request)
    result = await statements_list(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        template_id=opt_str(body.get("template_id")),
        document_id=opt_str(body.get("document_id")),
        limit=opt_int(body.get("limit"), 50),
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
        text_query=opt_str(body.get("text_query")),
        limit=opt_int(body.get("limit"), 200),
    )
    return JSONResponse(result)
