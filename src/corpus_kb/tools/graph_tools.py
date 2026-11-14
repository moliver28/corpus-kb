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

import asyncpg

from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = [
    "add_entity",
    "add_relation",
    "delete_entity",
    "delete_relation",
    "update_entity",
    "update_relation",
]


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
        tenant_id=uuid_arg(tenant_id, "tenant_id"),
        name=name,
        entity_type=entity_type,
        metadata=metadata or {},
    )
    handler = CommandHandler(load_config_or_pass(config), pg_pool)
    return await handler.handle_add_entity(cmd)


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
        tenant_id=uuid_arg(tenant_id, "tenant_id"),
        source_entity_id=uuid_arg(source_entity_id, "source_entity_id"),
        target_entity_id=uuid_arg(target_entity_id, "target_entity_id"),
        relation_type=relation_type,
        weight=weight,
        metadata=metadata or {},
    )
    handler = CommandHandler(load_config_or_pass(config), pg_pool)
    return await handler.handle_add_relation(cmd)


async def update_entity(
    pg_pool: asyncpg.Pool,
    entity_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    name: str | None = None,
    entity_type: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Update one entity's name and type.

    Present fields overwrite; absent fields keep the stored values. The
    write lands before the answer, so the next read sees it. Envelope:
    ``{"status", "entity_id", "name", "entity_type"}``.
    """
    from corpus_kb.tools.graph_read_tools import _resolve_graph_handler

    cfg = load_config_or_pass(config)
    handler = await _resolve_graph_handler(cfg, pg_pool)
    return await handler.handle_update_entity(
        uuid_arg(tenant_id, "tenant_id"),
        uuid_arg(entity_id, "entity_id"),
        name=name,
        entity_type=entity_type,
        metadata=metadata,
    )


async def delete_entity(
    pg_pool: asyncpg.Pool,
    entity_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Delete one entity and every relation linked to it.

    The delete is final and cannot be undone. The envelope reports how
    many relations the server removed:
    ``{"status", "entity_id", "relations_removed"}``.
    """
    from corpus_kb.tools.graph_read_tools import _resolve_graph_handler

    cfg = load_config_or_pass(config)
    handler = await _resolve_graph_handler(cfg, pg_pool)
    return await handler.handle_delete_entity(
        uuid_arg(tenant_id, "tenant_id"),
        uuid_arg(entity_id, "entity_id"),
    )


async def update_relation(
    pg_pool: asyncpg.Pool,
    relation_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    relation_type: str | None = None,
    weight: float | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Update one relation's type and weight.

    The endpoints cannot change: a new pair is a new relation — delete
    the old one, then create it. Envelope:
    ``{"status", "relation_id", "relation_type"}``.
    """
    from corpus_kb.tools.graph_read_tools import _resolve_graph_handler

    cfg = load_config_or_pass(config)
    handler = await _resolve_graph_handler(cfg, pg_pool)
    return await handler.handle_update_relation(
        uuid_arg(tenant_id, "tenant_id"),
        uuid_arg(relation_id, "relation_id"),
        relation_type=relation_type,
        weight=weight,
        metadata=metadata,
    )


async def delete_relation(
    pg_pool: asyncpg.Pool,
    relation_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Delete one relation by ID.

    The delete is final and cannot be undone. ``deleted`` in the
    envelope says whether a row was removed:
    ``{"status", "relation_id", "deleted"}``.
    """
    from corpus_kb.tools.graph_read_tools import _resolve_graph_handler

    cfg = load_config_or_pass(config)
    handler = await _resolve_graph_handler(cfg, pg_pool)
    return await handler.handle_delete_relation(
        uuid_arg(tenant_id, "tenant_id"),
        uuid_arg(relation_id, "relation_id"),
    )
