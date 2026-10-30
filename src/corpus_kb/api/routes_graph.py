"""Graph/knowledge route family (zero-drift split of api/http.py).

POST /api/entities, /api/relations
DELETE /api/documents/{doc_id}
POST /api/graph/search, /api/graph/bfs, /api/graph/neighbors
GET  /api/graph/relations/{entity_id}

Thin adapters: parse JSON, call the shared handler layer, return
JSONResponse. Every route runs through ``@route_errors`` (api._body) —
ONE error path. Handler imports stay inside the route bodies so tests can
monkeypatch the handler-module singletons.
"""

from __future__ import annotations

from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse

from corpus_kb.api._body import DEFAULT_TENANT_ID, parse_body, route_errors
from corpus_kb.domain.models import AddEntityCommand, AddRelationCommand


@route_errors
async def add_entity(request: Request) -> JSONResponse:
    """POST /api/entities — add an entity to the knowledge graph."""
    from corpus_kb.handlers.command_handler import get_command_handler

    body = await parse_body(request)
    cmd = AddEntityCommand(
        tenant_id=UUID(body.get("tenant_id", DEFAULT_TENANT_ID)),
        name=body["name"],
        entity_type=body.get("entity_type", "concept"),
        metadata=body.get("metadata", {}),
    )
    handler = get_command_handler()
    result = handler.handle_add_entity(cmd)
    return JSONResponse(result)


@route_errors
async def add_relation(request: Request) -> JSONResponse:
    """POST /api/relations — add a relation between two entities."""
    from corpus_kb.handlers.command_handler import get_command_handler

    body = await parse_body(request)
    cmd = AddRelationCommand(
        tenant_id=UUID(body.get("tenant_id", DEFAULT_TENANT_ID)),
        source_entity_id=UUID(body["source_entity_id"]),
        target_entity_id=UUID(body["target_entity_id"]),
        relation_type=body.get("relation_type", "related_to"),
        weight=body.get("weight", 1.0),
        metadata=body.get("metadata", {}),
    )
    handler = get_command_handler()
    result = handler.handle_add_relation(cmd)
    return JSONResponse(result)


@route_errors
async def delete_document(request: Request) -> JSONResponse:
    """DELETE /api/documents/{doc_id} — delete one document and its data."""
    from corpus_kb.domain.models import DeleteDocumentCommand
    from corpus_kb.handlers.command_handler import get_command_handler

    doc_id = request.path_params.get("doc_id")
    cmd = DeleteDocumentCommand(doc_id=UUID(doc_id))
    result = await get_command_handler().handle_delete_document(cmd)
    return JSONResponse(result)


@route_errors
async def search_graph(request: Request) -> JSONResponse:
    """POST /api/graph/search - search entities by name."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

    body = await parse_body(request)
    handler = get_graph_handler()
    results = await handler.handle_search_graph(
        UUID(body.get("tenant_id", DEFAULT_TENANT_ID)),
        body.get("query", ""),
        body.get("entity_type"),
        body.get("limit", 100),
    )
    return JSONResponse({"status": "success", "result": results})


@route_errors
async def bfs_traversal(request: Request) -> JSONResponse:
    """POST /api/graph/bfs - BFS traversal from an entity.

    DEPRECATED: use ``graph_traverse`` (POST /api/graph/traverse). This
    route stays one release and then goes away.
    """
    from corpus_kb.handlers.graph_handler import get_graph_handler

    body = await parse_body(request)
    handler = get_graph_handler()
    results = await handler.handle_bfs(
        UUID(body.get("tenant_id", DEFAULT_TENANT_ID)),
        UUID(body["start_entity_id"]),
        body.get("max_depth", 3),
    )
    return JSONResponse(
        {
            "status": "success",
            "result": results,
            "deprecated": "Use graph_traverse (POST /api/graph/traverse).",
        }
    )


@route_errors
async def graph_traverse(request: Request) -> JSONResponse:
    """POST /api/graph/traverse — walk from an entity up to the depth cap."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

    body = await parse_body(request)
    handler = get_graph_handler()
    raw_depth = body.get("max_depth")
    return JSONResponse(
        await handler.handle_graph_traverse(
            UUID(body.get("tenant_id", DEFAULT_TENANT_ID)),
            UUID(body["entity_id"]),
            max_depth=int(raw_depth) if raw_depth is not None else None,
            relation_types=body.get("relation_types"),
            entity_types=body.get("entity_types"),
            limit=body.get("limit", 100),
        )
    )


@route_errors
async def graph_neighbors(request: Request) -> JSONResponse:
    """POST /api/graph/neighbors — neighbors within 1-3 hops of an entity."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

    body = await parse_body(request)
    handler = get_graph_handler()
    return JSONResponse(
        await handler.handle_graph_neighbors(
            UUID(body.get("tenant_id", DEFAULT_TENANT_ID)),
            UUID(body["entity_id"]),
            hops=body.get("hops", 1),
            direction=body.get("direction", "both"),
            relation_types=body.get("relation_types"),
            entity_types=body.get("entity_types"),
            limit=body.get("limit", 100),
        )
    )


@route_errors
async def get_entity_relations(request: Request) -> JSONResponse:
    """GET /api/graph/relations/{entity_id} - get relations for an entity."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

    entity_id = request.path_params.get("entity_id")
    tenant_id = UUID(request.query_params.get("tenant_id", DEFAULT_TENANT_ID))
    raw_limit = request.query_params.get("limit")
    limit = int(raw_limit) if raw_limit else None
    handler = get_graph_handler()
    results = await handler.handle_get_entity_relations(
        tenant_id,
        UUID(entity_id),
        limit=limit,
    )
    return JSONResponse({"status": "success", "result": results})
