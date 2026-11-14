"""Graph-window CRUD read (pure move out of ``query_crud_reads``,
250-line rule; Phase-2 Wave 6).

``list_relations`` — the Map's edge window — as a module function;
``QueryHandler`` keeps its thin delegating method so every surface
keeps its exact call shape. The tenant-context and gateway notes in
``query_crud_reads``'s module docstring apply here unchanged (same
acquire shape: gateway ``transaction`` scope or
:func:`storage.tenant_conn.tenant_connection`).
"""

from __future__ import annotations

from typing import Any

import asyncpg

from corpus_kb.domain.models import ListRelationsQuery, RelationListResult
from corpus_kb.storage.gateway import SqlGateway, is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection


async def list_relations(
    pool: asyncpg.Pool | SqlGateway, query: ListRelationsQuery
) -> list[RelationListResult]:
    """List graph relations, one window (Phase-2 Wave 6 Map).

    The order is ``(relation_type, source_entity_id, target_entity_id,
    relation_id)`` — the id is unique, so two reads of one window answer
    in one total order (deterministic output, program rule 9). Endpoint
    names ride the join, so the Map and the CLI never issue one read per
    row to label an edge.
    """
    sql = """
        SELECT r.relation_id, r.source_entity_id, r.target_entity_id,
               r.relation_type, r.weight,
               s.name AS source_name, t.name AS target_name
        FROM relations r
        JOIN entities s ON s.entity_id = r.source_entity_id
        JOIN entities t ON t.entity_id = r.target_entity_id
        WHERE r.tenant_id = $1
        ORDER BY r.relation_type ASC, r.source_entity_id ASC,
                 r.target_entity_id ASC, r.relation_id ASC
        LIMIT $2 OFFSET $3
        """
    args = (str(query.tenant_id), query.limit, query.offset)
    if is_gateway(pool):
        async with pool.transaction(str(query.tenant_id)) as reader:
            rows = await reader.fetch(sql, *args)
        return _relations(rows)
    async with tenant_connection(pool, str(query.tenant_id)) as conn:
        rows = await conn.fetch(sql, *args)
        return _relations(rows)


def _relations(rows: list[Any]) -> list[RelationListResult]:
    return [
        RelationListResult(
            relation_id=row["relation_id"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            relation_type=row["relation_type"],
            weight=float(row["weight"]),
            source_name=row["source_name"],
            target_name=row["target_name"],
        )
        for row in rows
    ]
