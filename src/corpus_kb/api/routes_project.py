"""Project-table op routes (Track C2).

Three thin adapters, one per catalogue row: POST /api/project/{provision,
list, drop}. Each route parses JSON, calls the shared handler, and
returns JSONResponse through ``@route_errors`` — ONE error path.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_str, parse_body, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as _TENANT_DEFAULT


async def _pool():
    from corpus_kb.api.research_http import research_pool

    return await research_pool()


@route_errors
async def project_provision_route(request: Request) -> JSONResponse:
    """POST /api/project/provision — create raw_<slug> + registry row."""
    from corpus_kb.tools.project_tools import project_provision

    body = await parse_body(request)
    result = await project_provision(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        slug=opt_str(body.get("slug")) or "",
        project_id=opt_str(body.get("project_id")),
    )
    return JSONResponse(result)


@route_errors
async def project_list_route(request: Request) -> JSONResponse:
    """POST /api/project/list — the provisioned raw tables."""
    from corpus_kb.tools.project_tools import project_list

    body = await parse_body(request)
    result = await project_list(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
    )
    return JSONResponse(result)


@route_errors
async def project_drop_route(request: Request) -> JSONResponse:
    """POST /api/project/drop — drop one raw table + its registry row."""
    from corpus_kb.tools.project_tools import project_drop

    body = await parse_body(request)
    result = await project_drop(
        await _pool(),
        tenant_id=str(body.get("tenant_id", _TENANT_DEFAULT)),
        slug=opt_str(body.get("slug")) or "",
    )
    return JSONResponse(result)
