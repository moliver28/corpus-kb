"""HTTP adapter — Starlette REST API for Corpus-KB.

Thin adapter: parses JSON → constructs Pydantic command/query →
calls handler → returns JSONResponse. No business logic here.

Routes:
  POST /api/ingest/file, /api/ingest/text, /api/ingest/directory
  POST /api/search, /api/search/similar, /api/search/context
  POST /api/query/sql
  GET  /api/documents, /api/entities
  POST /api/entities, /api/relations
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from domain.models import (
    AddEntityCommand,
    AddRelationCommand,
    IngestDirectoryCommand,
    IngestFileCommand,
    IngestTextCommand,
    ListDocumentsQuery,
    ListEntitiesQuery,
    SQLQuery,
    SearchContextQuery,
    SearchQuery,
    SearchSimilarQuery,
)

logger = logging.getLogger(__name__)


def _get_tenant_id(request: Request) -> UUID:
    """Extract tenant_id from request, defaulting to placeholder."""
    body = request.state.body if hasattr(request.state, "body") else {}
    tid = body.get("tenant_id", "00000000-0000-0000-0000-000000000001")
    return UUID(tid)


async def _parse_body(request: Request) -> dict[str, Any]:
    """Parse JSON body, returning empty dict on failure."""
    try:
        data = await request.json()
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


async def ingest_file(request: Request) -> JSONResponse:
    """POST /api/ingest/file — ingest a file from disk."""
    from handlers.command_handler import get_command_handler

    body = await _parse_body(request)
    try:
        cmd = IngestFileCommand(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            file_path=body["file_path"],
            content=body.get("content"),
            source_type=body.get("source_type"),
        )
        handler = get_command_handler()
        result = handler.handle_ingest_file(cmd)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def ingest_text(request: Request) -> JSONResponse:
    """POST /api/ingest/text — ingest raw text."""
    from handlers.command_handler import get_command_handler

    body = await _parse_body(request)
    try:
        cmd = IngestTextCommand(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            text=body["text"],
            source=body.get("source", "raw_text"),
            source_type=body.get("source_type", "text"),
        )
        handler = get_command_handler()
        result = handler.handle_ingest_text(cmd)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def ingest_directory(request: Request) -> JSONResponse:
    """POST /api/ingest/directory — ingest all files in a directory."""
    from handlers.command_handler import get_command_handler

    body = await _parse_body(request)
    try:
        cmd = IngestDirectoryCommand(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            directory_path=body["directory_path"],
            recursive=body.get("recursive", True),
        )
        handler = get_command_handler()
        result = handler.handle_ingest_directory(cmd)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def search(request: Request) -> JSONResponse:
    """POST /api/search — hybrid vector + FTS search."""
    from handlers.query_handler import get_query_handler

    body = await _parse_body(request)
    try:
        query = SearchQuery(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            query=body["query"],
            k=body.get("k", 10),
            source_type=body.get("source_type"),
        )
        handler = get_query_handler()
        results = await handler.handle_search(query)
        return JSONResponse(
            {"status": "success", "result": [r.model_dump() for r in results]}
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def search_similar(request: Request) -> JSONResponse:
    """POST /api/search/similar — find chunks similar to a given chunk."""
    from handlers.query_handler import get_query_handler

    body = await _parse_body(request)
    try:
        query = SearchSimilarQuery(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            chunk_id=UUID(body["chunk_id"]),
            k=body.get("k", 10),
        )
        handler = get_query_handler()
        results = await handler.handle_search_similar(query)
        return JSONResponse(
            {"status": "success", "result": [r.model_dump() for r in results]}
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def search_context(request: Request) -> JSONResponse:
    """POST /api/search/context — search with surrounding context chunks."""
    from handlers.query_handler import get_query_handler

    body = await _parse_body(request)
    try:
        query = SearchContextQuery(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            query=body["query"],
            k=body.get("k", 5),
            context_chunks=body.get("context_chunks", 2),
        )
        handler = get_query_handler()
        results = await handler.handle_search_context(query)
        return JSONResponse(
            {"status": "success", "result": [r.model_dump() for r in results]}
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def query_sql(request: Request) -> JSONResponse:
    """POST /api/query/sql — execute a read-only SQL query."""
    from handlers.query_handler import get_query_handler

    body = await _parse_body(request)
    try:
        query = SQLQuery(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            sql=body["sql"],
            params=body.get("params", {}),
        )
        handler = get_query_handler()
        results = await handler.handle_sql_query(query)
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def list_documents(request: Request) -> JSONResponse:
    """GET /api/documents — list documents with pagination."""
    from handlers.query_handler import get_query_handler

    try:
        query = ListDocumentsQuery(
            tenant_id=UUID(
                request.query_params.get(
                    "tenant_id", "00000000-0000-0000-0000-000000000001"
                )
            ),
            limit=int(request.query_params.get("limit", 100)),
            offset=int(request.query_params.get("offset", 0)),
        )
        handler = get_query_handler()
        results = await handler.handle_list_documents(query)
        return JSONResponse(
            {"status": "success", "result": [r.model_dump() for r in results]}
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def list_entities(request: Request) -> JSONResponse:
    """GET /api/entities — list entities, optionally filtered by type."""
    from handlers.query_handler import get_query_handler

    try:
        query = ListEntitiesQuery(
            tenant_id=UUID(
                request.query_params.get(
                    "tenant_id", "00000000-0000-0000-0000-000000000001"
                )
            ),
            entity_type=request.query_params.get("entity_type"),
            limit=int(request.query_params.get("limit", 100)),
        )
        handler = get_query_handler()
        results = await handler.handle_list_entities(query)
        return JSONResponse(
            {"status": "success", "result": [r.model_dump() for r in results]}
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def add_entity(request: Request) -> JSONResponse:
    """POST /api/entities — add an entity to the knowledge graph."""
    from handlers.command_handler import get_command_handler

    body = await _parse_body(request)
    try:
        cmd = AddEntityCommand(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            name=body["name"],
            entity_type=body.get("entity_type", "concept"),
            metadata=body.get("metadata", {}),
        )
        handler = get_command_handler()
        result = handler.handle_add_entity(cmd)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def add_relation(request: Request) -> JSONResponse:
    """POST /api/relations — add a relation between two entities."""
    from handlers.command_handler import get_command_handler

    body = await _parse_body(request)
    try:
        cmd = AddRelationCommand(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            source_entity_id=UUID(body["source_entity_id"]),
            target_entity_id=UUID(body["target_entity_id"]),
            relation_type=body.get("relation_type", "related_to"),
            weight=body.get("weight", 1.0),
            metadata=body.get("metadata", {}),
        )
        handler = get_command_handler()
        result = handler.handle_add_relation(cmd)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def delete_document(request):
    from handlers.command_handler import get_command_handler
    from domain.models import DeleteDocumentCommand
    doc_id = request.path_params.get("doc_id")
    try:
        cmd = DeleteDocumentCommand(doc_id=UUID(doc_id))
        result = get_command_handler().handle_delete_document(cmd)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)

async def search_graph(request: Request) -> JSONResponse:
    """POST /api/graph/search - search entities by name."""
    from handlers.graph_handler import get_graph_handler
    body = await _parse_body(request)
    try:
        handler = get_graph_handler()
        results = await handler.handle_search_graph(
            UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            body.get("query", ""),
            body.get("entity_type"),
            body.get("limit", 100),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def bfs_traversal(request: Request) -> JSONResponse:
    """POST /api/graph/bfs - BFS traversal from an entity."""
    from handlers.graph_handler import get_graph_handler
    body = await _parse_body(request)
    try:
        handler = get_graph_handler()
        results = await handler.handle_bfs(
            UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            UUID(body["start_entity_id"]),
            body.get("max_depth", 3),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def get_entity_relations(request: Request) -> JSONResponse:
    """GET /api/graph/relations/{entity_id} - get relations for an entity."""
    from handlers.graph_handler import get_graph_handler
    entity_id = request.path_params.get("entity_id")
    try:
        handler = get_graph_handler()
        results = await handler.handle_get_entity_relations(
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID(entity_id),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def add_tag_route(request: Request) -> JSONResponse:
    """POST /api/tags - create a tag."""
    from handlers.tag_handler import get_tag_handler
    body = await _parse_body(request)
    try:
        handler = get_tag_handler()
        result = await handler.handle_add_tag(
            UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            body["name"],
            body.get("color"),
            body.get("description"),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def tag_document_route(request: Request) -> JSONResponse:
    """POST /api/documents/{doc_id}/tags - apply tag to document."""
    from handlers.tag_handler import get_tag_handler
    body = await _parse_body(request)
    doc_id = request.path_params.get("doc_id")
    try:
        handler = get_tag_handler()
        result = await handler.handle_tag_document(
            UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            UUID(doc_id),
            body["tag"],
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def get_document_tags_route(request: Request) -> JSONResponse:
    """GET /api/documents/{doc_id}/tags - list tags for a document."""
    from handlers.tag_handler import get_tag_handler
    doc_id = request.path_params.get("doc_id")
    try:
        handler = get_tag_handler()
        results = await handler.handle_get_document_tags(
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID(doc_id),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def set_metadata_route(request: Request) -> JSONResponse:
    """POST /api/metadata - set metadata key-value."""
    from handlers.tag_handler import get_tag_handler
    body = await _parse_body(request)
    try:
        handler = get_tag_handler()
        doc_id = UUID(body["doc_id"]) if body.get("doc_id") else None
        result = await handler.handle_set_metadata(
            UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            body["key"],
            body["value"],
            doc_id,
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def get_metadata_route(request: Request) -> JSONResponse:
    """GET /api/metadata - get metadata."""
    from handlers.tag_handler import get_tag_handler
    try:
        handler = get_tag_handler()
        key = request.query_params.get("key")
        doc_id = request.query_params.get("doc_id")
        results = await handler.handle_get_metadata(
            UUID("00000000-0000-0000-0000-000000000001"),
            key,
            UUID(doc_id) if doc_id else None,
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def list_versions(request: Request) -> JSONResponse:
    """GET /api/versions - list event store versions."""
    from handlers.versioning_handler import get_versioning_handler
    try:
        handler = get_versioning_handler()
        results = await handler.handle_list_versions(
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def get_stats(request: Request) -> JSONResponse:
    """GET /api/stats - get database statistics."""
    from handlers.versioning_handler import get_versioning_handler
    try:
        handler = get_versioning_handler()
        result = await handler.handle_get_stats(
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def sql_tables(request: Request) -> JSONResponse:
    """GET /api/tables - list all database tables."""
    from handlers.versioning_handler import get_versioning_handler
    try:
        handler = get_versioning_handler()
        results = await handler.handle_sql_tables(
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


async def document_stats(request: Request) -> JSONResponse:
    """GET /api/document-stats - aggregate document statistics."""
    from handlers.versioning_handler import get_versioning_handler
    try:
        handler = get_versioning_handler()
        result = await handler.handle_query_document_stats(
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "error_type": type(exc).__name__}, status_code=400)


def create_http_app() -> Starlette:
    """Create the Starlette HTTP application."""
    routes = [
        Route("/api/ingest/file", ingest_file, methods=["POST"]),
        Route("/api/ingest/text", ingest_text, methods=["POST"]),
        Route("/api/ingest/directory", ingest_directory, methods=["POST"]),
        Route("/api/search", search, methods=["POST"]),
        Route("/api/search/similar", search_similar, methods=["POST"]),
        Route("/api/search/context", search_context, methods=["POST"]),
        Route("/api/query/sql", query_sql, methods=["POST"]),
        Route("/api/documents", list_documents, methods=["GET"]),
        Route("/api/entities", list_entities, methods=["GET"]),
        Route("/api/entities", add_entity, methods=["POST"]),
        Route("/api/relations", add_relation, methods=["POST"]),
        Route("/api/documents/{doc_id}", delete_document, methods=["DELETE"]),
        Route("/api/graph/search", search_graph, methods=["POST"]),
        Route("/api/graph/bfs", bfs_traversal, methods=["POST"]),
        Route("/api/graph/relations/{entity_id}", get_entity_relations, methods=["GET"]),
        Route("/api/tags", add_tag_route, methods=["POST"]),
        Route("/api/documents/{doc_id}/tags", tag_document_route, methods=["POST"]),
        Route("/api/documents/{doc_id}/tags", get_document_tags_route, methods=["GET"]),
        Route("/api/metadata", set_metadata_route, methods=["POST"]),
        Route("/api/metadata", get_metadata_route, methods=["GET"]),
        Route("/api/versions", list_versions, methods=["GET"]),
        Route("/api/stats", get_stats, methods=["GET"]),
        Route("/api/tables", sql_tables, methods=["GET"]),
        Route("/api/document-stats", document_stats, methods=["GET"]),
    ]

    app = Starlette(routes=routes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    return app