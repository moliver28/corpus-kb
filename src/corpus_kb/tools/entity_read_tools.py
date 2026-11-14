"""MCP entity read tool: the ONE handler every surface routes through.

Phase-2 Wave 4: ``list_entities`` was handler-only — the GET route called
the query handler directly and no tool, CLI, socket, or MCP surface
existed. This module gives the op its real shared function; the MCP tool
is generated from the catalogue row that names this function.

Same house pattern as ``tools/ingest_tools.py:list_documents``: the query
handler is built over the CALLER'S storage seam (pool or gateway), so a
DuckDB gateway serves the read the same way a Postgres pool does.
"""

from __future__ import annotations

import asyncpg

from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["list_entities"]


async def list_entities(
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    entity_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    """List graph entities, optionally filtered by type.

    Delegates to ``QueryHandler.handle_list_entities`` — the same call
    GET /api/entities makes. Rows are ordered ``(name, entity_id)``.
    Envelope: ``{"status", "result"}``.
    """
    from corpus_kb.domain.models import ListEntitiesQuery
    from corpus_kb.handlers.query_handler import QueryHandler

    query = ListEntitiesQuery(
        tenant_id=uuid_arg(tenant_id, "tenant_id"),
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    )
    handler = QueryHandler(pg_pool, config=load_config_or_pass(config))
    results = await handler.handle_list_entities(query)
    return {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
