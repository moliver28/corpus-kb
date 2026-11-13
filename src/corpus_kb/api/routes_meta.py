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

from corpus_kb.api._body import opt_int, route_errors
from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.domain.models import (
    ListDocumentChunksQuery,
    ListDocumentsQuery,
    ListEntitiesQuery,
)


@route_errors
async def list_document_chunks(request: Request) -> JSONResponse:
    """GET /api/documents/{doc_id}/chunks — one document's chunk window.

    Phase-2 Wave 5: the drawer's NLP panel scopes its analysis runs to
    these chunk ids. Rows ride the ingest order (chunk_index, chunk_id).
    Counts ride the shared window-count guard (tools._count_input), so
    the HTTP arm answers the same typed rule the other surfaces answer.
    """
    from corpus_kb.handlers.query_handler import get_query_handler
    from corpus_kb.tools._count_input import limit_arg, offset_arg

    query = ListDocumentChunksQuery(
        tenant_id=UUID(request.query_params.get("tenant_id", DEFAULT_TENANT_ID)),
        doc_id=UUID(request.path_params.get("doc_id", "")),
        limit=limit_arg(request.query_params.get("limit")),
        offset=offset_arg(request.query_params.get("offset")),
    )
    handler = get_query_handler()
    results = await handler.handle_list_document_chunks(query)
    return JSONResponse(
        {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
    )


@route_errors
async def list_documents(request: Request) -> JSONResponse:
    """GET /api/documents — list documents with pagination."""
    from corpus_kb.handlers.query_handler import get_query_handler

    query = ListDocumentsQuery(
        tenant_id=UUID(request.query_params.get("tenant_id", DEFAULT_TENANT_ID)),
        limit=opt_int(request.query_params.get("limit"), 100, name="limit"),
        offset=opt_int(request.query_params.get("offset"), 0, name="offset"),
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
        limit=opt_int(request.query_params.get("limit"), 100, name="limit"),
        offset=opt_int(request.query_params.get("offset"), 0, name="offset"),
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
    """GET /api/stats — the corpus stats op (Phase-2 Wave 3 catalogue row)."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.versioning_tools import stats

    result = await stats(await research_pool())
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
    """GET /api/document-stats — the document-stats op (Wave 3 catalogue row)."""
    from corpus_kb.api.research_http import research_pool
    from corpus_kb.tools.versioning_tools import document_stats

    result = await document_stats(await research_pool())
    return JSONResponse(result)


@route_errors
async def doctor_route(request: Request) -> JSONResponse:
    """GET /api/doctor — read-only diagnostics from the CLI doctor probes."""
    from corpus_kb.tools.doctor_tools import doctor_snapshot

    result = await doctor_snapshot(None)
    return JSONResponse(result)
