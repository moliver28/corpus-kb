"""Graph handler — graph query tools backed by a pluggable GraphStore.

Public methods remain unchanged; internals delegate to the injected store:
  - handle_search_graph -> graph_store.search_entities
  - handle_bfs -> graph_store.bfs + graph_store.get_entity (to preserve shape)
  - handle_get_entity_relations -> graph_store.get_entity_relations +
    graph_store.get_entity (for source/target names)
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from corpus_kb.storage.graph_store import GraphStore
from corpus_kb.utils.models import Entity, Relation

logger = logging.getLogger(__name__)


class GraphHandler:
    """Graph query handler that delegates to an injected GraphStore backend."""

    def __init__(self, graph_store: GraphStore) -> None:
        self._graph_store = graph_store

    async def handle_search_graph(
        self,
        tenant_id: UUID,
        query: str,
        entity_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search entities by name (case-insensitive contains)."""
        entities = await self._graph_store.search_entities(query, entity_type)
        return [_entity_to_response(entity) for entity in entities[:limit]]

    async def handle_bfs(
        self, tenant_id: UUID, start_entity_id: UUID, max_depth: int = 3
    ) -> list[dict[str, Any]]:
        """BFS traversal from a starting entity; result shape preserved via get_entity."""
        result = await self._graph_store.bfs(str(start_entity_id), max_depth)
        visited = result.get("visited", {})
        response: list[dict[str, Any]] = []
        for entity_id, depth in visited.items():
            entity = await self._graph_store.get_entity(entity_id)
            if entity is None:
                entity = Entity(
                    entity_id=entity_id,
                    name="",
                    entity_type="",
                    source_type="code",
                )
            response.append(
                {
                    "entity_id": entity_id,
                    "name": entity.name,
                    "entity_type": entity.entity_type,
                    "depth": depth,
                }
            )
        return sorted(response, key=lambda row: (row["depth"], row["name"]))

    async def handle_get_entity_relations(
        self, tenant_id: UUID, entity_id: UUID
    ) -> list[dict[str, Any]]:
        """Get all relations for an entity (both outgoing and incoming)."""
        relations = await self._graph_store.get_entity_relations(str(entity_id))
        return [
            await _relation_to_response(self._graph_store, rel) for rel in relations
        ]


def _entity_to_response(entity: Entity) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "entity_type": entity.entity_type,
        "metadata": entity.metadata,
    }


async def _relation_to_response(
    store: GraphStore, relation: Relation
) -> dict[str, Any]:
    source = await store.get_entity(relation.source_entity_id)
    target = await store.get_entity(relation.target_entity_id)
    return {
        "relation_id": relation.relation_id,
        "relation_type": relation.relation_type,
        "weight": relation.weight,
        "source_entity_id": relation.source_entity_id,
        "target_entity_id": relation.target_entity_id,
        "source_name": source.name if source else "",
        "target_name": target.name if target else "",
    }


# ============================================================================
# Singleton
# ============================================================================

_graph_handler: Optional["GraphHandler"] = None


def get_graph_handler() -> "GraphHandler":
    global _graph_handler
    if _graph_handler is None:
        raise RuntimeError(
            "GraphHandler not initialized. Call set_graph_handler() during startup."
        )
    return _graph_handler


def set_graph_handler(handler: "GraphHandler") -> None:
    global _graph_handler
    _graph_handler = handler


def reset_graph_handler() -> None:
    global _graph_handler
    _graph_handler = None
