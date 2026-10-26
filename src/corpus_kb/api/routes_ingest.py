"""Ingest route family (zero-drift split of api/http.py).

POST /api/ingest/file, /api/ingest/text, /api/ingest/directory, /api/ingest/url
GET  /api/ingest/status
POST /api/ingest/reembed

Thin adapters: parse JSON, call the shared tool/handler layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests can
monkeypatch the handler-module singletons.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import opt_bool, parse_body, route_errors
from corpus_kb.domain.models import (
    IngestDirectoryCommand,
    IngestFileCommand,
    IngestTextCommand,
)


@route_errors
async def ingest_file(request: Request) -> JSONResponse:
    """POST /api/ingest/file — ingest a file from disk."""
    from corpus_kb.handlers.command_handler import get_command_handler

    body = await parse_body(request)
    cmd = IngestFileCommand(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        file_path=body["file_path"],
        content=body.get("content"),
        source_type=body.get("source_type"),
        validate_mode=str(body.get("validate", "off")),
    )
    handler = get_command_handler()
    result = await handler.handle_ingest_file(cmd)
    return JSONResponse(result)


@route_errors
async def ingest_text(request: Request) -> JSONResponse:
    """POST /api/ingest/text — ingest raw text."""
    from corpus_kb.handlers.command_handler import get_command_handler

    body = await parse_body(request)
    cmd = IngestTextCommand(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        text=body["text"],
        source=body.get("source", "raw_text"),
        source_type=body.get("source_type", "text"),
        validate_mode=str(body.get("validate", "off")),
    )
    handler = get_command_handler()
    result = await handler.handle_ingest_text(cmd)
    return JSONResponse(result)


@route_errors
async def ingest_directory(request: Request) -> JSONResponse:
    """POST /api/ingest/directory — ingest all files in a directory."""
    from corpus_kb.handlers.command_handler import get_command_handler

    body = await parse_body(request)
    cmd = IngestDirectoryCommand(
        tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        directory_path=body["directory_path"],
        recursive=body.get("recursive", True),
        validate_mode=str(body.get("validate", "off")),
    )
    handler = get_command_handler()
    result = await handler.handle_ingest_directory(cmd)
    return JSONResponse(result)


@route_errors
async def ingest_url_route(request: Request) -> JSONResponse:
    """POST /api/ingest/url — fetch (or crawl) a website and ingest its pages."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.ingest_tools import ingest_url

    body: dict[str, Any] = await parse_body(request)
    result = await ingest_url(
        str(body["url"]),
        await research_pool(),
        tenant_id=body.get("tenant_id", "00000000-0000-0000-0000-000000000001"),
        crawl=opt_bool(body.get("crawl"), False),
        source_type=body.get("source_type"),
        validate=str(body.get("validate", "off")),
    )
    return JSONResponse(result)


@route_errors
async def ingest_status_route(request: Request) -> JSONResponse:
    """GET /api/ingest/status — recent ingest_runs ledger entries + degraded counts."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import ingest_surface_tools

    limit = int(request.query_params.get("limit", ingest_surface_tools.DEFAULT_RUN_LIMIT))
    result = await ingest_surface_tools.ingest_status(
        await research_pool(),
        tenant_id=request.query_params.get("tenant_id", "00000000-0000-0000-0000-000000000001"),
        limit=limit,
    )
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)


@route_errors
async def ingest_reembed_route(request: Request) -> JSONResponse:
    """POST /api/ingest/reembed — re-embed degraded chunks (honest summary)."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools import ingest_surface_tools

    body = await parse_body(request)
    raw_limit = body.get("limit")
    result = await ingest_surface_tools.ingest_reembed(
        await research_pool(),
        tenant_id=body.get("tenant_id", "00000000-0000-0000-0000-000000000001"),
        limit=int(raw_limit) if raw_limit is not None else None,
        doc_id=body.get("doc_id"),
    )
    # Same contract as ingest-status: a pre-migration database is 503,
    # never a silent 200.
    status_code = 503 if result.get("status") == "unavailable" else 200
    return JSONResponse(result, status_code=status_code)
