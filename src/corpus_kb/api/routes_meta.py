"""Metadata/listing route family (zero-drift split of api/http.py).

GET /api/documents, /api/entities            (query-handler listing reads)
GET /api/versions, /api/stats, /api/tables, /api/document-stats
                                             (versioning-handler diagnostics)
GET /api/doctor                              (read-only probes; UI SPA Task 5a)

Thin adapters: parse query params, call the shared handler layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests can
monkeypatch the handler-module singletons.
"""

from __future__ import annotations

from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.domain.models import ListDocumentsQuery, ListEntitiesQuery


@route_errors
async def list_documents(request: Request) -> JSONResponse:
    """GET /api/documents — list documents with pagination."""
    from corpus_kb.handlers.query_handler import get_query_handler

    query = ListDocumentsQuery(
        tenant_id=UUID(request.query_params.get("tenant_id", DEFAULT_TENANT_ID)),
        limit=int(request.query_params.get("limit", 100)),
        offset=int(request.query_params.get("offset", 0)),
    )
    handler = get_query_handler()
    results = await handler.handle_list_documents(query)
    return JSONResponse(
        {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
    )


@route_errors
async def list_entities(request: Request) -> JSONResponse:
    """GET /api/entities — list entities, optionally filtered by type."""
    from corpus_kb.handlers.query_handler import get_query_handler

    query = ListEntitiesQuery(
        tenant_id=UUID(request.query_params.get("tenant_id", DEFAULT_TENANT_ID)),
        entity_type=request.query_params.get("entity_type"),
        limit=int(request.query_params.get("limit", 100)),
    )
    handler = get_query_handler()
    results = await handler.handle_list_entities(query)
    return JSONResponse(
        {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
    )


@route_errors
async def list_versions(request: Request) -> JSONResponse:
    """GET /api/versions - list event store versions."""
    from corpus_kb.handlers.versioning_handler import get_versioning_handler

    handler = get_versioning_handler()
    results = await handler.handle_list_versions(
        UUID(DEFAULT_TENANT_ID),
    )
    return JSONResponse({"status": "success", "result": results})


@route_errors
async def get_stats(request: Request) -> JSONResponse:
    """GET /api/stats - get database statistics."""
    from corpus_kb.handlers.versioning_handler import get_versioning_handler

    handler = get_versioning_handler()
    result = await handler.handle_get_stats(
        UUID(DEFAULT_TENANT_ID),
    )
    return JSONResponse(result)


@route_errors
async def sql_tables(request: Request) -> JSONResponse:
    """GET /api/tables - list all database tables."""
    from corpus_kb.handlers.versioning_handler import get_versioning_handler

    handler = get_versioning_handler()
    results = await handler.handle_sql_tables(
        UUID(DEFAULT_TENANT_ID),
    )
    return JSONResponse({"status": "success", "result": results})


@route_errors
async def document_stats(request: Request) -> JSONResponse:
    """GET /api/document-stats - aggregate document statistics."""
    from corpus_kb.handlers.versioning_handler import get_versioning_handler

    handler = get_versioning_handler()
    result = await handler.handle_query_document_stats(
        UUID(DEFAULT_TENANT_ID),
    )
    return JSONResponse(result)


@route_errors
async def doctor_route(request: Request) -> JSONResponse:
    """GET /api/doctor — read-only diagnostics from the CLI doctor probes."""
    from corpus_kb.tools.doctor_tools import doctor_snapshot

    result = await doctor_snapshot(None)
    return JSONResponse(result)
