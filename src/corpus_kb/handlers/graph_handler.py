"""Graph handler — graph query tools backed by a pluggable GraphStore.

Public methods remain unchanged; internals delegate to the injected store:
  - handle_search_graph -> graph_store.search_entities
  - handle_bfs -> graph_store.bfs + graph_store.get_entity (to preserve shape)
  - handle_get_entity_relations -> graph_store.get_entity_relations +
    graph_store.get_entity (for source/target names)
  - handle_graph_neighbors / handle_graph_traverse -> the first-class hop
    traversal (storage-portability program §5.2): depths 1..3 take the
    fixed-chain neighbors fast path, deeper walks take the recursive
    traverse, capped by ``graph.max_hops`` (default 5, ceiling 25).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from corpus_kb.errors import ValidationError
from corpus_kb.storage.graph_sql import DEFAULT_LIMIT, DIRECTIONS, MAX_NEIGHBOR_HOPS
from corpus_kb.storage.graph_store import GraphStore
from corpus_kb.utils.models import Entity, Relation

logger = logging.getLogger(__name__)

# graph.max_hops (config, Wave A): the traversal ceiling. Default 5,
# ceiling 25 = today's AGE cap (validate_configs.MAX_HOPS_CEILING).
DEFAULT_MAX_HOPS = 5
MAX_HOPS_CEILING = 25
MIN_TRAVERSE_DEPTH = 2


def configured_max_hops(config: dict[str, Any] | None) -> int:
    """Resolve graph.max_hops from config; clamp to the 1..25 window."""
    graph = (config or {}).get("graph") or {}
    raw = graph.get("max_hops", DEFAULT_MAX_HOPS)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return DEFAULT_MAX_HOPS
    try:
        hops = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_HOPS
    return max(1, min(hops, MAX_HOPS_CEILING))


def _int_field(name: str, value: object, low: int, high: int) -> int:
    """Validate one integer field; raise the taxonomy's ValidationError."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(f"{name} must be an integer from {low} to {high}.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be an integer from {low} to {high}.") from exc
    if not low <= number <= high:
        raise ValidationError(f"{name} must be an integer from {low} to {high}.")
    return number


def _name_list(value: object) -> list[str] | None:
    """Normalize an optional list of type names (strings only)."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value] if value else None
    if isinstance(value, (list, tuple)):
        names = [str(item) for item in value if str(item)]
        return names or None
    raise ValidationError("Type filters must be a list of names.")


class GraphHandler:
    """Graph query handler that delegates to an injected GraphStore backend."""

    def __init__(self, graph_store: GraphStore, config: dict[str, Any] | None = None) -> None:
        self._graph_store = graph_store
        self._max_hops = configured_max_hops(config)

    async def handle_search_graph(
        self,
        tenant_id: UUID,
        query: str,
        entity_type: str | None = None,
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
        return [await _relation_to_response(self._graph_store, rel) for rel in relations]

    async def handle_graph_neighbors(
        self,
        tenant_id: UUID,
        entity_id: UUID,
        hops: int = 1,
        direction: str = "both",
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Neighbor rows within hops 1..3, ordered (depth, name, entity_id)."""
        hop_count = _int_field("hops", hops, 1, MAX_NEIGHBOR_HOPS)
        if direction not in DIRECTIONS:
            raise ValidationError("direction must be one of: in, out, both.")
        max_limit = _int_field("limit", limit, 1, 10_000)
        rows = await self._graph_store.neighbors(
            str(entity_id),
            hop_count,
            direction,
            _name_list(relation_types),
            _name_list(entity_types),
            max_limit,
        )
        return {"status": "success", "result": rows}

    async def handle_graph_traverse(
        self,
        tenant_id: UUID,
        entity_id: UUID,
        max_depth: int | None = None,
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Walk up to graph.max_hops; depths 1..3 take the fast path.

        Returns the neighbor row shape plus ``truncated``: true when the
        walk filled the limit, so callers know the graph continued.
        """
        depth = (
            self._max_hops
            if max_depth is None
            else _int_field("max_depth", max_depth, MIN_TRAVERSE_DEPTH, self._max_hops)
        )
        max_limit = _int_field("limit", limit, 1, 10_000)
        if depth <= MAX_NEIGHBOR_HOPS:
            rows = await self._graph_store.neighbors(
                str(entity_id),
                depth,
                "both",
                _name_list(relation_types),
                _name_list(entity_types),
                max_limit + 1,
            )
        else:
            rows = await self._graph_store.traverse(
                str(entity_id),
                depth,
                _name_list(relation_types),
                _name_list(entity_types),
                max_limit + 1,
            )
        truncated = len(rows) > max_limit
        return {"status": "success", "result": rows[:max_limit], "truncated": truncated}


def _entity_to_response(entity: Entity) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "entity_type": entity.entity_type,
        "metadata": entity.metadata,
    }


async def _relation_to_response(store: GraphStore, relation: Relation) -> dict[str, Any]:
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

_graph_handler: GraphHandler | None = None


def get_graph_handler() -> GraphHandler:
    global _graph_handler
    if _graph_handler is None:
        raise RuntimeError("GraphHandler not initialized. Call set_graph_handler() during startup.")
    return _graph_handler


def set_graph_handler(handler: GraphHandler) -> None:
    global _graph_handler
    _graph_handler = handler


def reset_graph_handler() -> None:
    global _graph_handler
    _graph_handler = None
