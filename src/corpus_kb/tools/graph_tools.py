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

import logging

import asyncpg

from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["add_entity", "add_relation"]

_log = logging.getLogger(__name__)


async def _projected_entity_id(
    pg_pool: asyncpg.Pool, tenant_id: str, name: str, entity_type: str
) -> str | None:
    """The row id the projection materialized for (tenant, name, type).

    The eventsourcing recorder logs each domain event under its own
    (per-event) originator in addition to the aggregate's row, so the
    aggregate ids a handler returns are NOT always the ids the read model
    carries. The projected id is the one every other surface (search,
    hops, relations FK) is keyed on, so callers get it. Read strictly
    through the CALLER'S pool: an empty result keeps the envelope exactly
    what the handler produced (test stubs yield empty and see no change).
    """
    try:
        rows = await pg_pool.fetch(
            """
            SELECT entity_id::text AS entity_id
            FROM entities
            WHERE tenant_id = $1 AND name = $2 AND entity_type = $3
            """,
            uuid_arg(tenant_id, "tenant_id"),
            name,
            entity_type,
        )
        if not rows:
            return None
        return str(rows[0]["entity_id"])
    except Exception as exc:
        _log.warning("projected entity lookup unavailable: %s", exc)
        return None


async def _entity_exists(pg_pool: asyncpg.Pool, tenant_id: str, entity_id: str) -> bool:
    """Existence check in the PROJECTED entities table (pool fetch only)."""
    rows = await pg_pool.fetch(
        "SELECT 1 FROM entities WHERE tenant_id = $1 AND entity_id = $2::uuid",
        uuid_arg(tenant_id, "tenant_id"),
        entity_id,
    )
    return bool(rows)


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
    result = handler.handle_add_entity(cmd)
    # Advance ALL read models, then return the PROJECTED id (search/hops/
    # relations key on it), keeping the aggregate id under agggregate_id
    # for callers that audit the event flow.
    from corpus_kb.projections.catchup import advance_all_projections

    try:
        await advance_all_projections(pg_pool, cmd.tenant_id)
    except Exception as exc:  # fails open: infra-unavailable keeps the envelope
        _log.warning("add_entity projection catchup skipped: %s", exc)
    projected = await _projected_entity_id(pg_pool, tenant_id, name, entity_type)
    if projected is not None and projected != result["entity_id"]:
        result["entity_id"] = projected
    return result


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
    # Read models must be current BEFORE validation: add_entity just ahead
    # of this call projected its row, and the relations FK targets that
    # projected id space.
    from corpus_kb.projections.catchup import advance_all_projections

    try:
        await advance_all_projections(pg_pool, cmd.tenant_id)
    except Exception as exc:  # fails open: infra-unavailable keeps the model
        _log.warning("add_relation projection catchup skipped: %s", exc)
    try:
        view_rows = await pg_pool.fetch(
            "SELECT count(*)::int AS n FROM entities WHERE tenant_id = $1",
            uuid_arg(tenant_id, "tenant_id"),
        )
        view = bool(view_rows) and int(view_rows[0]["n"]) > 0
    except Exception as exc:
        _log.warning("entity projection view unavailable: %s", exc)
        view = False  # no view: validation degrades to the legacy contract
    missing = [
        label
        for label, entity_id in (
            ("source_entity_id", source_entity_id),
            ("target_entity_id", target_entity_id),
        )
        if view and not await _entity_exists(pg_pool, tenant_id, entity_id)
    ]
    if missing:
        from corpus_kb.config import ConfigError

        raise ConfigError(
            f"{', '.join(missing)} not in the projected entities table. "
            "add_entity now returns the PROJECTED id; re-create the relation "
            "with ids from its envelope."
        )
    handler = CommandHandler(load_config_or_pass(config), pg_pool)
    result = handler.handle_add_relation(cmd)
    try:
        await advance_all_projections(pg_pool, cmd.tenant_id)
    except Exception as exc:
        _log.warning("add_relation post-write catchup skipped: %s", exc)
    return result
