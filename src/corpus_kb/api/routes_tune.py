"""Tune-op route (Living Corpus W5).

One thin adapter: POST /api/extraction-tune. Parses JSON, calls the
shared handler, returns JSONResponse through ``@route_errors`` — ONE
error path.
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


def _opt_float(body: dict[str, Any], key: str, default: float) -> float:
    raw = body.get(key)
    try:
        return float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


@route_errors
async def extraction_tune_route(request: Request) -> JSONResponse:
    """POST /api/extraction-tune — mine confirmed evidence into artifacts."""
    from corpus_kb.tools.extraction_tune_tools import extraction_tune

    body = await parse_body(request)
    result = await extraction_tune(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        method=str(body.get("method", "exemplar")),
        profile=opt_str(body.get("profile")),
        min_confidence=_opt_float(body, "min_confidence", 0.8),
        limit=opt_int(body.get("limit"), 8),
        dry_run=opt_bool(body.get("dry_run"), False),
    )
    return JSONResponse(result)
