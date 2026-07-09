"""Graph handler — graph tools using Apache AGE Cypher queries.

Uses the AGE extension for Cypher-based graph traversal:
  - search_graph: search entities by name
  - bfs: BFS traversal from a starting entity
  - get_entity_relations: get all relations for an entity
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class GraphHandler:
    """Graph query handler using Apache AGE Cypher."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def handle_search_graph(
        self, tenant_id: UUID, query: str, entity_type: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Search entities by name (case-insensitive contains)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            if entity_type:
                rows = await conn.fetch(
                    """SELECT entity_id, name, entity_type, metadata
                       FROM entities WHERE tenant_id = $1 AND name ILIKE $2 AND entity_type = $3
                       ORDER BY name LIMIT $4""",
                    str(tenant_id), f"%{query}%", entity_type, limit,
                )
            else:
                rows = await conn.fetch(
                    """SELECT entity_id, name, entity_type, metadata
                       FROM entities WHERE tenant_id = $1 AND name ILIKE $2
                       ORDER BY name LIMIT $3""",
                    str(tenant_id), f"%{query}%", limit,
                )
            return [dict(r) for r in rows]

    async def handle_bfs(
        self, tenant_id: UUID, start_entity_id: UUID, max_depth: int = 3
    ) -> list[dict[str, Any]]:
        """BFS traversal using recursive CTE (works without AGE Cypher too)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            rows = await conn.fetch(
                """WITH RECURSIVE bfs AS (
                       SELECT e.entity_id, e.name, e.entity_type, 0 as depth
                       FROM entities e WHERE e.entity_id = $1 AND e.tenant_id = $2
                       UNION ALL
                       SELECT e2.entity_id, e2.name, e2.entity_type, b.depth + 1
                       FROM bfs b
                       JOIN relations r ON r.source_entity_id = b.entity_id AND r.tenant_id = $2
                       JOIN entities e2 ON e2.entity_id = r.target_entity_id AND e2.tenant_id = $2
                       WHERE b.depth < $3
                   )
                   SELECT DISTINCT entity_id, name, entity_type, depth FROM bfs ORDER BY depth, name""",
                str(start_entity_id), str(tenant_id), max_depth,
            )
            return [dict(r) for r in rows]

    async def handle_get_entity_relations(
        self, tenant_id: UUID, entity_id: UUID
    ) -> list[dict[str, Any]]:
        """Get all relations for an entity (both outgoing and incoming)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            rows = await conn.fetch(
                """SELECT r.relation_id, r.relation_type, r.weight,
                          r.source_entity_id, r.target_entity_id,
                          se.name as source_name, te.name as target_name
                   FROM relations r
                   JOIN entities se ON r.source_entity_id = se.entity_id
                   JOIN entities te ON r.target_entity_id = te.entity_id
                   WHERE r.tenant_id = $1
                     AND (r.source_entity_id = $2 OR r.target_entity_id = $2)
                   ORDER BY r.relation_type""",
                str(tenant_id), str(entity_id),
            )
            return [dict(r) for r in rows]

# ============================================================================
# Singleton
# ============================================================================

_graph_handler: Optional["GraphHandler"] = None


def get_graph_handler() -> "GraphHandler":
    global _graph_handler
    if _graph_handler is None:
        raise RuntimeError("GraphHandler not initialized. Call set_graph_handler() during startup.")
    return _graph_handler


def set_graph_handler(handler: "GraphHandler") -> None:
    global _graph_handler
    _graph_handler = handler


def reset_graph_handler() -> None:
    global _graph_handler
    _graph_handler = None
