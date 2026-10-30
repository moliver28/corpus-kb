"""Meta-embed route (Living Corpus W6).

One thin adapter: POST /api/analysis/embed-meta. Parses JSON, calls
the shared handler, returns JSONResponse through ``@route_errors`` —
ONE error path.
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
async def analysis_embed_meta_route(request: Request) -> JSONResponse:
    """POST /api/analysis/embed-meta — build the corpus meta aggregates."""
    from corpus_kb.tools.meta_embed_tools import analysis_embed_meta

    body = await parse_body(request)
    raw_kinds = body.get("kinds")
    result = await analysis_embed_meta(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        kinds=str(raw_kinds) if raw_kinds is not None else None,
    )
    return JSONResponse(result)
