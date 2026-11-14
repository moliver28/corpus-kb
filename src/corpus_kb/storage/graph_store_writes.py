"""Postgres write helpers for graph update/delete (Phase-2 Wave 8).

Split out of ``graph_store.py`` for the 250-line rule (the ``graph_sql``
pattern: pure functions over one connection, imported by the store).

Every function takes the tenant and the primitive columns it may write —
the parameter list IS the column whitelist:

- Entity updates write only ``name``, ``entity_type``, ``metadata``.
- Relation updates write only ``relation_type``, ``weight``, ``metadata``.

Provenance columns (``source_document_id``, ``chunk_id``, ``confidence``,
``extractor_id``, ``model_version``, ``prompt_version``) are absent from
every SET clause by construction. Endpoints are absent too: a changed
endpoint pair is a different triple — delete, then re-create.

Deletes are HARD deletes (no tombstones; the caller confirms). The
response reports what the delete removed; there is no audit trail by
decision (see the GraphHandler docstrings).
"""

from __future__ import annotations

import json

import asyncpg

from corpus_kb.errors import ConflictError

from ..utils.models import Entity, Relation

ENTITY_CONFLICT_MSG = "Choose a different name and type. A name and type pair must be unique."
RELATION_CONFLICT_MSG = (
    "A relation of this type between these endpoints in this chunk already exists."
)


async def update_entity_by_id(
    conn: asyncpg.Connection,
    tenant_id: str,
    entity_id: str,
    name: str,
    entity_type: str,
    metadata: dict[str, object],
) -> Entity | None:
    """Update one entity's whitelisted columns; None when the id is unknown.

    A (tenant, name, entity_type) collision with a different entity raises
    ConflictError.
    """
    try:
        row = await conn.fetchrow(
            """
            UPDATE entities SET name = $2, entity_type = $3, metadata = $4
            WHERE entity_id = $1 AND tenant_id = $5
            RETURNING entity_id::text, name, entity_type, metadata::text,
                      source_document_id::text
            """,
            entity_id,
            name,
            entity_type,
            json.dumps(metadata),
            tenant_id,
        )
    except asyncpg.exceptions.UniqueViolationError as exc:
        raise ConflictError(ENTITY_CONFLICT_MSG) from exc
    if not row:
        return None
    loaded = json.loads(row["metadata"]) if row["metadata"] else {}
    return Entity(
        entity_id=str(row["entity_id"]),
        name=str(row["name"]),
        entity_type=str(row["entity_type"]),
        source_type="code",
        source_document_id=row["source_document_id"],
        metadata=loaded,
    )


async def delete_entity_cascade(
    conn: asyncpg.Connection,
    tenant_id: str,
    entity_id: str,
) -> int | None:
    """Delete one entity and every relation linked to it, in one transaction.

    The caller owns the transaction scope. Returns the exact number of
    relations removed, or None when the entity was already absent.
    """
    exists = await conn.fetchrow(
        "SELECT 1 FROM entities WHERE entity_id = $1 AND tenant_id = $2",
        entity_id,
        tenant_id,
    )
    if not exists:
        return None
    removed = await conn.fetch(
        """
        DELETE FROM relations
        WHERE tenant_id = $1 AND (source_entity_id = $2 OR target_entity_id = $2)
        RETURNING relation_id
        """,
        tenant_id,
        entity_id,
    )
    await conn.execute(
        "DELETE FROM entities WHERE entity_id = $1 AND tenant_id = $2",
        entity_id,
        tenant_id,
    )
    return len(removed)


async def update_relation_by_id(
    conn: asyncpg.Connection,
    tenant_id: str,
    relation_id: str,
    relation_type: str,
    weight: float,
    metadata: dict[str, object],
) -> Relation | None:
    """Update one relation's whitelisted columns; None when the id is unknown.

    A duplicate (tenant, source, target, type, chunk) triple raises
    ConflictError.
    """
    try:
        row = await conn.fetchrow(
            """
            UPDATE relations SET relation_type = $2, weight = $3, metadata = $4
            WHERE relation_id = $1 AND tenant_id = $5
            RETURNING relation_id::text, source_entity_id::text,
                      target_entity_id::text, relation_type, weight,
                      metadata::text
            """,
            relation_id,
            relation_type,
            weight,
            json.dumps(metadata),
            tenant_id,
        )
    except asyncpg.exceptions.UniqueViolationError as exc:
        raise ConflictError(RELATION_CONFLICT_MSG) from exc
    if not row:
        return None
    loaded = json.loads(row["metadata"]) if row["metadata"] else {}
    return Relation(
        relation_id=str(row["relation_id"]),
        source_entity_id=str(row["source_entity_id"]),
        target_entity_id=str(row["target_entity_id"]),
        relation_type=str(row["relation_type"]),
        weight=float(row["weight"]),
        metadata=loaded,
    )


async def delete_relation_by_id(
    conn: asyncpg.Connection,
    tenant_id: str,
    relation_id: str,
) -> bool:
    """Delete one relation by ID. True when a row was removed."""
    row = await conn.fetchrow(
        """
        DELETE FROM relations
        WHERE relation_id = $1 AND tenant_id = $2
        RETURNING relation_id
        """,
        relation_id,
        tenant_id,
    )
    return row is not None


async def get_relation_by_id(
    conn: asyncpg.Connection,
    tenant_id: str,
    relation_id: str,
) -> Relation | None:
    """Fetch one relation by ID (the handler's read-merge-write seam)."""
    row = await conn.fetchrow(
        """
        SELECT relation_id::text, source_entity_id::text,
               target_entity_id::text, relation_type, weight, metadata::text
        FROM relations
        WHERE relation_id = $1 AND tenant_id = $2
        """,
        relation_id,
        tenant_id,
    )
    if not row:
        return None
    loaded = json.loads(row["metadata"]) if row["metadata"] else {}
    return Relation(
        relation_id=str(row["relation_id"]),
        source_entity_id=str(row["source_entity_id"]),
        target_entity_id=str(row["target_entity_id"]),
        relation_type=str(row["relation_type"]),
        weight=float(row["weight"]),
        metadata=loaded,
    )
