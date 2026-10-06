"""HTTP adapter — Starlette REST API for Corpus-KB.

Thin adapter: parses JSON → constructs Pydantic command/query →
calls handler → returns JSONResponse. No business logic here.

Routes:
  POST /api/ingest/file, /api/ingest/text, /api/ingest/directory
  POST /api/search, /api/search/similar, /api/search/context
  POST /api/query/sql
  POST /api/embed
  POST /api/coding/embed-codebook, /api/coding/pool, /api/coding/code-batch
  POST /api/coding/reliability, /api/coding/calibrate-floors, /api/coding/saturation
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

from corpus_kb.domain.models import (
    AddEntityCommand,
    AddRelationCommand,
    IngestDirectoryCommand,
    IngestFileCommand,
    IngestTextCommand,
    ListDocumentsQuery,
    ListEntitiesQuery,
    RoutedQuery,
    SearchContextQuery,
    SearchQuery,
    SearchSimilarQuery,
    SQLQuery,
    VerifyAnswerQuery,
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
    from corpus_kb.handlers.command_handler import get_command_handler

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
    from corpus_kb.handlers.command_handler import get_command_handler

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
    from corpus_kb.handlers.command_handler import get_command_handler

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
    from corpus_kb.handlers.query_handler import get_query_handler

    body = await _parse_body(request)
    try:
        query = SearchQuery(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            query=body["query"],
            k=body.get("k", 10),
            source_type=body.get("source_type"),
            self_query=body.get("self_query"),
        )
        handler = get_query_handler()
        results = await handler.handle_search(query)
        return JSONResponse({"status": "success", "result": [r.model_dump() for r in results]})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def search_similar(request: Request) -> JSONResponse:
    """POST /api/search/similar — find chunks similar to a given chunk."""
    from corpus_kb.handlers.query_handler import get_query_handler

    body = await _parse_body(request)
    try:
        query = SearchSimilarQuery(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            chunk_id=UUID(body["chunk_id"]),
            k=body.get("k", 10),
        )
        handler = get_query_handler()
        results = await handler.handle_search_similar(query)
        return JSONResponse({"status": "success", "result": [r.model_dump() for r in results]})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def search_context(request: Request) -> JSONResponse:
    """POST /api/search/context — search with surrounding context chunks."""
    from corpus_kb.handlers.query_handler import get_query_handler

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
        return JSONResponse({"status": "success", "result": [r.model_dump() for r in results]})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def verify(request: Request) -> JSONResponse:
    """POST /api/verify — claim-level groundedness verification against cited chunks."""
    from corpus_kb.handlers.query_handler import get_query_handler

    body = await _parse_body(request)
    try:
        query = VerifyAnswerQuery(
            answer=body.get("answer", ""), chunk_ids=body.get("chunk_ids", [])
        )
        result = await get_query_handler().handle_verify_answer(query)
        return JSONResponse({"status": "success", "result": result.model_dump(mode="json")})
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


async def route_query(request: Request) -> JSONResponse:
    """POST /api/query — adaptive routing across vector/SQL/graph backends."""
    from corpus_kb.handlers.router_handler import get_router_handler

    body = await _parse_body(request)
    try:
        query = RoutedQuery(
            tenant_id=UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001")),
            query=body["query"],
            k=body.get("k", 10),
        )
        handler = get_router_handler()
        result = await handler.handle_routed_query(query)
        return JSONResponse({"status": "success", "result": result.model_dump(mode="json")})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def query_sql(request: Request) -> JSONResponse:
    """POST /api/query/sql — execute a read-only SQL query."""
    from corpus_kb.handlers.query_handler import get_query_handler

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
    from corpus_kb.handlers.query_handler import get_query_handler

    try:
        query = ListDocumentsQuery(
            tenant_id=UUID(
                request.query_params.get("tenant_id", "00000000-0000-0000-0000-000000000001")
            ),
            limit=int(request.query_params.get("limit", 100)),
            offset=int(request.query_params.get("offset", 0)),
        )
        handler = get_query_handler()
        results = await handler.handle_list_documents(query)
        return JSONResponse({"status": "success", "result": [r.model_dump() for r in results]})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def list_entities(request: Request) -> JSONResponse:
    """GET /api/entities — list entities, optionally filtered by type."""
    from corpus_kb.handlers.query_handler import get_query_handler

    try:
        query = ListEntitiesQuery(
            tenant_id=UUID(
                request.query_params.get("tenant_id", "00000000-0000-0000-0000-000000000001")
            ),
            entity_type=request.query_params.get("entity_type"),
            limit=int(request.query_params.get("limit", 100)),
        )
        handler = get_query_handler()
        results = await handler.handle_list_entities(query)
        return JSONResponse({"status": "success", "result": [r.model_dump() for r in results]})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def add_entity(request: Request) -> JSONResponse:
    """POST /api/entities — add an entity to the knowledge graph."""
    from corpus_kb.handlers.command_handler import get_command_handler

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
    from corpus_kb.handlers.command_handler import get_command_handler

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
    from corpus_kb.domain.models import DeleteDocumentCommand
    from corpus_kb.handlers.command_handler import get_command_handler

    doc_id = request.path_params.get("doc_id")
    try:
        cmd = DeleteDocumentCommand(doc_id=UUID(doc_id))
        result = get_command_handler().handle_delete_document(cmd)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)


async def search_graph(request: Request) -> JSONResponse:
    """POST /api/graph/search - search entities by name."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

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
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def bfs_traversal(request: Request) -> JSONResponse:
    """POST /api/graph/bfs - BFS traversal from an entity."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

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
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def get_entity_relations(request: Request) -> JSONResponse:
    """GET /api/graph/relations/{entity_id} - get relations for an entity."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

    entity_id = request.path_params.get("entity_id")
    try:
        handler = get_graph_handler()
        results = await handler.handle_get_entity_relations(
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID(entity_id),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def add_tag_route(request: Request) -> JSONResponse:
    """POST /api/tags - create a tag."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

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
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def tag_document_route(request: Request) -> JSONResponse:
    """POST /api/documents/{doc_id}/tags - apply tag to document."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

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
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def get_document_tags_route(request: Request) -> JSONResponse:
    """GET /api/documents/{doc_id}/tags - list tags for a document."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

    doc_id = request.path_params.get("doc_id")
    try:
        handler = get_tag_handler()
        results = await handler.handle_get_document_tags(
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID(doc_id),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def set_metadata_route(request: Request) -> JSONResponse:
    """POST /api/metadata - set metadata key-value."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

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
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def get_metadata_route(request: Request) -> JSONResponse:
    """GET /api/metadata - get metadata."""
    from corpus_kb.handlers.tag_handler import get_tag_handler

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
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def list_versions(request: Request) -> JSONResponse:
    """GET /api/versions - list event store versions."""
    from corpus_kb.handlers.versioning_handler import get_versioning_handler

    try:
        handler = get_versioning_handler()
        results = await handler.handle_list_versions(
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def get_stats(request: Request) -> JSONResponse:
    """GET /api/stats - get database statistics."""
    from corpus_kb.handlers.versioning_handler import get_versioning_handler

    try:
        handler = get_versioning_handler()
        result = await handler.handle_get_stats(
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def sql_tables(request: Request) -> JSONResponse:
    """GET /api/tables - list all database tables."""
    from corpus_kb.handlers.versioning_handler import get_versioning_handler

    try:
        handler = get_versioning_handler()
        results = await handler.handle_sql_tables(
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        return JSONResponse({"status": "success", "result": results})
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def document_stats(request: Request) -> JSONResponse:
    """GET /api/document-stats - aggregate document statistics."""
    from corpus_kb.handlers.versioning_handler import get_versioning_handler

    try:
        handler = get_versioning_handler()
        result = await handler.handle_query_document_stats(
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def embed_text(request: Request) -> JSONResponse:
    """POST /api/embed — embed text with optional instruction prefix."""
    import asyncio

    from corpus_kb.handlers.coding_handler import load_instruct
    from corpus_kb.rag.embedder import OllamaEmbedder

    body = await _parse_body(request)
    text = str(body.get("text", ""))
    requested_instructed = bool(body.get("instructed", False))

    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    try:
        instruct = load_instruct()
        payload = instruct(text) if (instruct is not None and requested_instructed) else text
        # Per-request embedder: single texts gain nothing from a shared cache,
        # and the blocking Ollama round trip runs off the event loop.
        vector = await asyncio.to_thread(OllamaEmbedder().embed, payload)
        return JSONResponse(
            {
                "vector": vector,
                "instructed": instruct is not None and requested_instructed,
                "dimensions": len(vector),
            }
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )


async def coding_embed_codebook(request: Request) -> JSONResponse:
    """POST /api/coding/embed-codebook — Load codebook and embed each code."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    try:
        body = await _parse_body(request)
        tenant_id = UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001"))
        codebook = body.get("codebook") or {}
        result = await get_coding_handler().handle_embed_codebook(tenant_id, codebook)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=400,
        )


async def coding_pool(request: Request) -> JSONResponse:
    """POST /api/coding/pool — Run pooling (keyword + similarity)."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    try:
        body = await _parse_body(request)
        tenant_id = UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001"))
        codebook_version_id = UUID(str(body["codebook_version_id"]))
        result = await get_coding_handler().handle_pool(tenant_id, codebook_version_id)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=400,
        )


async def coding_code_batch(request: Request) -> JSONResponse:
    """POST /api/coding/code-batch — Run batch coding with routing."""
    from corpus_kb.coding.confidence_routing import Bands
    from corpus_kb.handlers.coding_handler import get_coding_handler

    try:
        body = await _parse_body(request)
        tenant_id = UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001"))
        raw_bands = body.get("bands")
        bands = Bands(**raw_bands) if raw_bands else None
        result = await get_coding_handler().handle_code_batch(
            tenant_id,
            list(body.get("cells", [])),
            coder=body.get("coder"),
            model=body.get("model"),
            batch_id=body.get("batch_id"),
            coding_pass=int(body.get("coding_pass", 1)),
            bands=bands,
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=400,
        )


async def coding_reliability(request: Request) -> JSONResponse:
    """POST /api/coding/reliability — Compute reliability (alpha/kappa)."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    try:
        body = await _parse_body(request)
        tenant_id = UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001"))
        result = await get_coding_handler().handle_reliability(
            tenant_id,
            per_code_min=int(body.get("per_code_min", 5)),
            seed=int(body.get("seed", 17)),
            code_universe=body.get("code_universe"),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=400,
        )


async def coding_calibrate_floors(request: Request) -> JSONResponse:
    """POST /api/coding/calibrate-floors — Calibrate one code's pool/residual floor."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    try:
        body = await _parse_body(request)
        tenant_id = UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001"))
        result = await get_coding_handler().handle_calibrate_floors(
            tenant_id,
            code_id=str(body["code_id"]),
            codebook_version_id=UUID(str(body["codebook_version_id"])),
            positive_chunk_ids=[UUID(str(c)) for c in body.get("positive_chunk_ids", [])],
            negative_chunk_ids=[UUID(str(c)) for c in body.get("negative_chunk_ids", [])],
            min_pos=int(body.get("min_pos", 10)),
            global_fallback=body.get("global_fallback"),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=400,
        )


async def coding_saturation(request: Request) -> JSONResponse:
    """POST /api/coding/saturation — ISR + stop-rule saturation signal."""
    from corpus_kb.handlers.coding_handler import get_coding_handler

    try:
        body = await _parse_body(request)
        tenant_id = UUID(body.get("tenant_id", "00000000-0000-0000-0000-000000000001"))
        result = await get_coding_handler().handle_saturation(
            tenant_id,
            batch_id=body.get("batch_id"),
            threshold=float(body.get("threshold", 0.05)),
            min_samples=int(body.get("min_samples", 50)),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=400,
        )


def create_http_app() -> Starlette:
    """Create the Starlette HTTP application."""
    routes = [
        Route("/api/ingest/file", ingest_file, methods=["POST"]),
        Route("/api/ingest/text", ingest_text, methods=["POST"]),
        Route("/api/ingest/directory", ingest_directory, methods=["POST"]),
        Route("/api/search", search, methods=["POST"]),
        Route("/api/search/similar", search_similar, methods=["POST"]),
        Route("/api/search/context", search_context, methods=["POST"]),
        Route("/api/verify", verify, methods=["POST"]),
        Route("/api/query", route_query, methods=["POST"]),
        Route("/api/query/sql", query_sql, methods=["POST"]),
        Route("/api/embed", embed_text, methods=["POST"]),
        Route("/api/coding/embed-codebook", coding_embed_codebook, methods=["POST"]),
        Route("/api/coding/pool", coding_pool, methods=["POST"]),
        Route("/api/coding/code-batch", coding_code_batch, methods=["POST"]),
        Route("/api/coding/reliability", coding_reliability, methods=["POST"]),
        Route("/api/coding/calibrate-floors", coding_calibrate_floors, methods=["POST"]),
        Route("/api/coding/saturation", coding_saturation, methods=["POST"]),
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
