"""The entity/relation row inserters (pure move out of documents_projection).

``project_entity`` and ``project_relation`` are the byte-identical bodies
of ``DocumentsProjection._project_entity`` / ``_project_relation``; the
projection methods delegate. The tenant advisory lock closes the READ
COMMITTED duplicate window on the chunkless-relations delete+insert (the
engine split is documented on ``tenant_write_lock``).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from corpus_kb.projections._locks import tenant_write_lock
from corpus_kb.storage.dialect import Dialect
from corpus_kb.storage.gateway import SqlGateway

# Serializes the chunkless-relations delete+insert across writers.
_RELATIONS_LOCK_KEY = "documents-projection:relations"


async def project_entity(gateway: SqlGateway, tenant_id: UUID, payload: dict[str, Any]) -> None:
    """Insert into the entities table."""
    async with gateway.transaction(str(tenant_id)) as tx:
        await tx.execute(
            """
            INSERT INTO entities
            (entity_id, tenant_id, name, entity_type, metadata)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
            """,
            str(payload.get("aggregate_id", "")),
            str(tenant_id),
            payload.get("name", ""),
            payload.get("entity_type", "concept"),
            json.dumps(payload.get("metadata", {})),
        )


async def project_relation(
    gateway: SqlGateway, dialect: Dialect, tenant_id: UUID, payload: dict[str, Any]
) -> None:
    """Insert into the relations table, deduped on the narrow triple key.

    Migration 008 dropped the constraint the old upsert targeted, so
    delete-then-insert in one scope restores the dedupe portably (the
    delete touches chunkless rows only, so provenance relations are
    never rewritten). The tenant advisory lock closes the READ
    COMMITTED duplicate window; ``projections/_locks.py`` has the
    mechanics and the engine split.
    """
    args = (
        str(payload.get("aggregate_id", "")),
        str(tenant_id),
        str(payload.get("source_entity_id", "")),
        str(payload.get("target_entity_id", "")),
        payload.get("relation_type", "related_to"),
        payload.get("weight", 1.0),
        json.dumps(payload.get("metadata", {})),
    )
    async with (
        gateway.transaction(str(tenant_id)) as tx,
        # The lock enters inside the tx scope; the delete binds only the
        # four key columns because DuckDB rejects unused parameters.
        tenant_write_lock(tx, dialect, str(tenant_id), _RELATIONS_LOCK_KEY),
    ):
        await tx.execute(
            """
            DELETE FROM relations
            WHERE tenant_id = $1 AND source_entity_id = $2
              AND target_entity_id = $3 AND relation_type = $4
              AND chunk_id IS NULL
            """,
            args[1],
            args[2],
            args[3],
            args[4],
        )
        await tx.execute(
            """
            INSERT INTO relations
            (relation_id, tenant_id, source_entity_id, target_entity_id,
             relation_type, weight, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            *args,
        )
