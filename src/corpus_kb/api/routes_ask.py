"""Ask route (UX program Wave 1): POST /api/ask.

Thin adapter, api/routes_search.py style: parse JSON, call the shared
tool layer (``tools/ask_tools.ask`` — the ONE function every surface
routes through), return JSONResponse. The route runs through
``@route_errors`` (api._body) — ONE error path. The tool import stays
inside the route body so tests can monkeypatch the tool module.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import DEFAULT_TENANT_ID, error_response, parse_body, route_errors


@route_errors
async def ask(request: Request) -> JSONResponse:
    """POST /api/ask — grounded answer with sentence-level citations."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ask_tools import ask as ask_tool

    body = await parse_body(request)
    query = str(body.get("query", "")).strip()
    if not query:
        return error_response("validation_error", "query is required")
    result = await ask_tool(
        query,
        tenant_id=str(body.get("tenant_id", DEFAULT_TENANT_ID)),
        k=body.get("k"),
        pg_pool=await research_pool(),
    )
    return JSONResponse(result)
