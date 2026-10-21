"""MCP graph read tools: the ONE handler every surface routes through.

The storage-portability program's §5.1 catalogue births: ``search_graph``,
``get_entity_relations``, ``graph_neighbors``, and ``graph_traverse``
previously had no tool layer — HTTP called the handler singleton directly
and the ghost MCP names in ``SERVER_AUTO_APPROVE`` had no function. This
module gives all four their real shared function; the MCP tools are
generated from the catalogue rows that name these functions.

Same house pattern as ``tools/search_tools.py``: built over the CALLER'S
pool. When the server has wired the graph-handler singleton every surface
resolves it; standalone callers (CLI, MCP stdio, tests) get the same
handler built over their pool via ``storage.graph_factory.create_graph_store``
(the composition root the server wiring itself calls — this module never
imports ``server_wiring``, which would drag the api adapters in).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import asyncpg

from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

if TYPE_CHECKING:
    from corpus_kb.handlers.graph_handler import GraphHandler

__all__ = [
    "get_entity_relations",
    "graph_neighbors",
    "graph_traverse",
    "search_graph",
]


async def _resolve_graph_handler(cfg: dict[str, object], pool: asyncpg.Pool) -> GraphHandler:
    """Return the wired graph handler, or build one over this pool."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

    try:
        return get_graph_handler()
    except RuntimeError:
        pass

    from corpus_kb.handlers.graph_handler import GraphHandler
    from corpus_kb.storage.graph_factory import create_graph_store

    graph_store = await create_graph_store(cfg, pool)
    return GraphHandler(graph_store, cfg)


async def search_graph(
    pg_pool: asyncpg.Pool,
    query: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    entity_type: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    """Search graph entities by name.

    Delegates to ``GraphHandler.handle_search_graph`` — the same call POST
    /api/graph/search and the socket ``search_graph`` method make. Rows are
    ordered ``(name, entity_id)``. Envelope: ``{"status", "result"}``.
    """
    cfg = load_config_or_pass(config)
    handler = await _resolve_graph_handler(cfg, pg_pool)
    rows = await handler.handle_search_graph(
        UUID(tenant_id), query, entity_type=entity_type, limit=limit
    )
    return {"status": "success", "result": rows}


async def get_entity_relations(
    pg_pool: asyncpg.Pool,
    entity_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int | None = None,
) -> dict[str, object]:
    """List one entity's relations.

    Delegates to ``GraphHandler.handle_get_entity_relations`` — the same
    call GET /api/graph/relations/{id} and the socket method make. Rows
    are ordered ``relation_id``. Envelope: ``{"status", "result"}``;
    ``limit=None`` keeps the uncapped read the route always had.
    """
    cfg = load_config_or_pass(config)
    handler = await _resolve_graph_handler(cfg, pg_pool)
    rows = await handler.handle_get_entity_relations(UUID(tenant_id), UUID(entity_id), limit=limit)
    return {"status": "success", "result": rows}


async def graph_neighbors(
    pg_pool: asyncpg.Pool,
    entity_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    hops: int = 1,
    direction: str = "both",
    relation_types: list[str] | None = None,
    entity_types: list[str] | None = None,
    limit: int = 100,
) -> dict[str, object]:
    """List neighbors within 1 to 3 hops of one entity.

    Delegates to ``GraphHandler.handle_graph_neighbors`` — the same call
    POST /api/graph/neighbors and the socket method make. Rows are ordered
    ``(depth, name, entity_id)`` at their shallowest depth. Envelope:
    ``{"status", "result"}``.
    """
    cfg = load_config_or_pass(config)
    handler = await _resolve_graph_handler(cfg, pg_pool)
    return await handler.handle_graph_neighbors(
        UUID(tenant_id),
        UUID(entity_id),
        hops=hops,
        direction=direction,
        relation_types=relation_types,
        entity_types=entity_types,
        limit=limit,
    )


async def graph_traverse(
    pg_pool: asyncpg.Pool,
    entity_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    max_depth: int | None = None,
    relation_types: list[str] | None = None,
    entity_types: list[str] | None = None,
    limit: int = 100,
) -> dict[str, object]:
    """Walk the graph from one entity up to the depth cap.

    Delegates to ``GraphHandler.handle_graph_traverse`` — the same call
    POST /api/graph/traverse and the socket method make. Depths 1..3 take
    the neighbors fast path; deeper walks recurse. ``max_depth=None``
    resolves ``graph.max_hops`` (default 5). Envelope:
    ``{"status", "result", "truncated"}``.
    """
    cfg = load_config_or_pass(config)
    handler = await _resolve_graph_handler(cfg, pg_pool)
    return await handler.handle_graph_traverse(
        UUID(tenant_id),
        UUID(entity_id),
        max_depth=max_depth,
        relation_types=relation_types,
        entity_types=entity_types,
        limit=limit,
    )
