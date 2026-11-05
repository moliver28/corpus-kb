"""The analysis question-router route (routing program, wave 3).

POST /api/analysis/route — one thin adapter over the shared handler
(``tools.analysis_route_tools.analysis_route``), the SAME function the
MCP + socket + CLI surfaces call. Parses JSON, calls the handler, and
returns JSONResponse through ``@route_errors`` (api._body) — ONE error
path. The handler import stays inside the route body so tests can
monkeypatch the tool-module functions. A raw non-string question passes
through: the handler owns the typed translation.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


async def _pool() -> Any:
    from corpus_kb.api.research_http import research_pool

    return await research_pool()


@route_errors
async def analysis_route_route(request: Request) -> JSONResponse:
    """POST /api/analysis/route — route one plain-language question."""
    from corpus_kb.tools.analysis_route_tools import analysis_route

    body = await parse_body(request)
    question = body.get("question")
    method_id = body.get("method_id")
    result = await analysis_route(
        await _pool(),
        tenant_id=body.get("tenant_id", _TENANT_DEFAULT),
        question="" if question is None else question,
        method_id=str(method_id) if method_id is not None else None,
    )
    return JSONResponse(result)
