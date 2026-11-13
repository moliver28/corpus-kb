"""MCP relation read tool: the ONE handler every surface routes through.

Phase-2 Wave 6: the Map's edge window. ``relations_list`` is that read —
the GET route ``/api/graph/relations``, the socket ``list_relations``
method, the MCP tool, and the CLI command all answer through this
function.

Same house pattern as ``tools/entity_read_tools.py:list_entities``: the
query handler is built over the CALLER'S storage seam (pool or gateway),
so a DuckDB gateway serves the read the same way a Postgres pool does.
"""

from __future__ import annotations

import asyncpg

from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.tools._count_input import limit_arg, offset_arg
from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["relations_list"]


async def relations_list(
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    """List graph relations, one window.

    Delegates to ``QueryHandler.handle_list_relations`` — the same call
    GET /api/graph/relations makes. Rows are ordered ``(relation_type,
    source_entity_id, target_entity_id, relation_id)`` and carry both
    endpoint names. The shared window-count guard (``tools._count_input``)
    owns the >=1 rule on MCP, socket, and CLI. HTTP is the one surface
    that accepts ``limit=0`` and answers an empty window (the entity
    window read shares that split). Envelope: ``{"status", "result"}``.
    """
    from corpus_kb.domain.models import ListRelationsQuery
    from corpus_kb.handlers.query_handler import QueryHandler

    query = ListRelationsQuery(
        tenant_id=uuid_arg(tenant_id, "tenant_id"),
        limit=limit_arg(limit),
        offset=offset_arg(offset),
    )
    handler = QueryHandler(pg_pool, config=load_config_or_pass(config))
    results = await handler.handle_list_relations(query)
    return {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
