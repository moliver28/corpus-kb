"""MCP graph tools: the ONE handler every surface routes through.

Full-surface parity wave: ``add_entity`` and ``add_relation`` previously
had NO tool-layer function — HTTP and socket both called the command
handler directly and the parity catalogue pinned that builder. This module
gives both operations their real MCP surface; the parity catalogue now
runs the tool leg through these functions.

Same house pattern as ``tools/search_tools.py``: built over the CALLER'S
pool (the command-handler singleton pins the first pool it sees, and these
tools are handed a pool explicitly). The envelope is exactly what POST
/api/entities and POST /api/relations return.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["add_entity", "add_relation"]


async def add_entity(
    pg_pool: asyncpg.Pool,
    name: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    entity_type: str = "concept",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Add one entity to the knowledge graph via the aggregate.

    Delegates to ``CommandHandler.handle_add_entity`` — the same call
    POST /api/entities and the socket ``add_entity`` method make — so
    every surface returns the same envelope:
    ``{"status", "entity_id", "name", "entity_type"}``.
    """
    from corpus_kb.domain.models import AddEntityCommand
    from corpus_kb.handlers.command_handler import CommandHandler

    cmd = AddEntityCommand(
        tenant_id=UUID(tenant_id),
        name=name,
        entity_type=entity_type,
        metadata=metadata or {},
    )
    handler = CommandHandler(load_config_or_pass(config), pg_pool)
    return handler.handle_add_entity(cmd)


async def add_relation(
    pg_pool: asyncpg.Pool,
    source_entity_id: str,
    target_entity_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    relation_type: str = "related_to",
    weight: float = 1.0,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Add one relation between two entities via the aggregate.

    Delegates to ``CommandHandler.handle_add_relation`` — the same call
    POST /api/relations and the socket ``add_relation`` method make — so
    every surface returns the same envelope:
    ``{"status", "relation_id", "relation_type"}``.
    """
    from corpus_kb.domain.models import AddRelationCommand
    from corpus_kb.handlers.command_handler import CommandHandler

    cmd = AddRelationCommand(
        tenant_id=UUID(tenant_id),
        source_entity_id=UUID(source_entity_id),
        target_entity_id=UUID(target_entity_id),
        relation_type=relation_type,
        weight=weight,
        metadata=metadata or {},
    )
    handler = CommandHandler(load_config_or_pass(config), pg_pool)
    return handler.handle_add_relation(cmd)
