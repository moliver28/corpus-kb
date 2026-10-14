"""Tag + metadata route family (zero-drift split of api/http.py).

POST /api/tags, /api/metadata            GET  /api/metadata
POST+GET /api/documents/{doc_id}/tags

Thin adapters: parse JSON, call the shared handler layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests can
monkeypatch the handler-module singletons.
"""

from __future__ import annotations

from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import parse_body, route_errors


@route_errors
async def add_tag_route(request: Request) -> JSONResponse:
    """POST /api/tags - create a tag."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

    body = await parse_body(request)
    handler = get_tag_handler()
    result = await handler.handle_add_tag(
        UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        body["name"],
        body.get("color"),
        body.get("description"),
    )
    return JSONResponse(result)


@route_errors
async def tag_document_route(request: Request) -> JSONResponse:
    """POST /api/documents/{doc_id}/tags - apply tag to document."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

    body = await parse_body(request)
    doc_id = request.path_params.get("doc_id")
    handler = get_tag_handler()
    result = await handler.handle_tag_document(
        UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        UUID(doc_id),
        body["tag"],
    )
    return JSONResponse(result)


@route_errors
async def get_document_tags_route(request: Request) -> JSONResponse:
    """GET /api/documents/{doc_id}/tags - list tags for a document."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

    doc_id = request.path_params.get("doc_id")
    handler = get_tag_handler()
    results = await handler.handle_get_document_tags(
        UUID("00000000-0000-0000-0000-000000000001"),
        UUID(doc_id),
    )
    return JSONResponse({"status": "success", "result": results})


@route_errors
async def set_metadata_route(request: Request) -> JSONResponse:
    """POST /api/metadata - set metadata key-value."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

    body = await parse_body(request)
    handler = get_tag_handler()
    doc_id = UUID(body["doc_id"]) if body.get("doc_id") else None
    result = await handler.handle_set_metadata(
        UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
        body["key"],
        body["value"],
        doc_id,
    )
    return JSONResponse(result)


@route_errors
async def get_metadata_route(request: Request) -> JSONResponse:
    """GET /api/metadata - get metadata."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

    handler = get_tag_handler()
    key = request.query_params.get("key")
    doc_id = request.query_params.get("doc_id")
    results = await handler.handle_get_metadata(
        UUID("00000000-0000-0000-0000-000000000001"),
        key,
        UUID(doc_id) if doc_id else None,
    )
    return JSONResponse({"status": "success", "result": results})
