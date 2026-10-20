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
handler built over their pool via ``server_wiring.create_graph_store``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import asyncpg

from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

if TYPE_CHECKING:
    from corpus_kb.handlers.graph_handler import GraphHandler

__all__ = ["search_graph"]


async def _resolve_graph_handler(cfg: dict[str, object], pool: asyncpg.Pool) -> GraphHandler:
    """Return the wired graph handler, or build one over this pool."""
    from corpus_kb.handlers.graph_handler import get_graph_handler

    try:
        return get_graph_handler()
    except RuntimeError:
        pass

    from corpus_kb.handlers.graph_handler import GraphHandler
    from corpus_kb.server_wiring import create_graph_store

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
