"""Graph traversal — BFS/DFS with cycle detection and path tracking.

Uses the GraphStore interface for all operations, so it works with
any graph backend (SQLite, GraphQLite, LatticeDB).

Note: GraphStore.get_neighbors() returns list[dict], not list[Relation].
See graph_store.py for the exact dict shapes at different depths.
"""

from __future__ import annotations

from typing import Any, Optional

from storage.graph_store import GraphStore


def _extract_relations(neighbor: dict) -> list[dict]:
    """Extract relation info from a neighbor dict, handling both depth formats."""
    if "relation" in neighbor:
        return [neighbor["relation"]]
    return neighbor.get("relations", [])


def _get_entity_id(neighbor: dict) -> str:
    """Extract entity ID from neighbor dict, handling both depth formats."""
    if "entity" in neighbor:
        ent = neighbor["entity"]
        return ent.get("entity_id", "") if isinstance(ent, dict) else getattr(ent, "entity_id", "")
    return neighbor.get("entity_id", "")


def _get_entity_name(neighbor: dict) -> str:
    if "entity" in neighbor:
        ent = neighbor["entity"]
        return ent.get("name", "") if isinstance(ent, dict) else getattr(ent, "name", "")
    return neighbor.get("entity_name", neighbor.get("name", ""))


def _get_entity_type(neighbor: dict) -> str:
    if "entity" in neighbor:
        ent = neighbor["entity"]
        return ent.get("type", "") if isinstance(ent, dict) else getattr(ent, "type", "")
    return neighbor.get("entity_type", neighbor.get("type", ""))


def bfs_traverse(
    graph: GraphStore,
    start_id: str,
    max_depth: int = 5,
    relation_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """BFS traversal from start_id with cycle detection.

    Args:
        graph: GraphStore instance
        start_id: Starting entity ID
        max_depth: Maximum traversal depth
        relation_types: Optional filter for specific relation types

    Returns:
        List of dicts: {depth, entity_id, entity_name, entity_type, path, relations}
    """
    visited: set[str] = set()
    results: list[dict[str, Any]] = []

    queue: list[tuple[str, int, list[str]]] = [(start_id, 0, [start_id])]

    while queue:
        entity_id, depth, path = queue.pop(0)

        if entity_id in visited:
            continue
        if depth > max_depth:
            continue

        visited.add(entity_id)

        # Get entity details
        entity = graph.get_entity(entity_id)
        if not entity:
            continue

        # Get relations
        neighbors = graph.get_neighbors(entity_id)

        # Build relation list from neighbors
        relations = []
        next_entity_ids: list[str] = []
        for n in neighbors:
            rels = _extract_relations(n)
            for r in rels:
                if relation_types and r.get("relation_type") not in relation_types:
                    continue
                relations.append({
                    "relation_id": r.get("relation_id", ""),
                    "relation_type": r.get("relation_type", ""),
                    "weight": r.get("weight", 1.0),
                    "direction": r.get("direction", ""),
                })
            nid = _get_entity_id(n)
            if nid and nid not in visited:
                next_entity_ids.append(nid)

        results.append({
            "depth": depth,
            "entity_id": entity.entity_id,
            "entity_name": entity.name,
            "entity_type": entity.type,
            "path": path.copy(),
            "relations": relations,
        })

        for nid in next_entity_ids:
            queue.append((nid, depth + 1, path + [nid]))

    return results


def dfs_traverse(
    graph: GraphStore,
    start_id: str,
    max_depth: int = 5,
    relation_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """DFS traversal from start_id with cycle detection.

    Same return format as bfs_traverse.
    """
    visited: set[str] = set()
    results: list[dict[str, Any]] = []

    def _dfs(entity_id: str, depth: int, path: list[str]):
        if entity_id in visited or depth > max_depth:
            return

        visited.add(entity_id)

        entity = graph.get_entity(entity_id)
        if not entity:
            return

        neighbors = graph.get_neighbors(entity_id)
        relations = []
        next_ids: list[str] = []
        for n in neighbors:
            rels = _extract_relations(n)
            for r in rels:
                if relation_types and r.get("relation_type") not in relation_types:
                    continue
                relations.append({
                    "relation_id": r.get("relation_id", ""),
                    "relation_type": r.get("relation_type", ""),
                    "weight": r.get("weight", 1.0),
                    "direction": r.get("direction", ""),
                })
            nid = _get_entity_id(n)
            if nid and nid not in visited:
                next_ids.append(nid)

        results.append({
            "depth": depth,
            "entity_id": entity.entity_id,
            "entity_name": entity.name,
            "entity_type": entity.type,
            "path": path.copy(),
            "relations": relations,
        })

        for nid in next_ids:
            _dfs(nid, depth + 1, path + [nid])

    _dfs(start_id, 0, [start_id])
    return results


def find_shortest_path(
    graph: GraphStore,
    from_id: str,
    to_id: str,
    max_depth: int = 10,
) -> Optional[list[str]]:
    """BFS shortest path between two entities.

    Returns list of entity IDs forming the path, or None if no path exists.
    """
    if from_id == to_id:
        return [from_id]

    visited: set[str] = {from_id}
    queue: list[tuple[str, list[str]]] = [(from_id, [from_id])]

    while queue:
        current_id, path = queue.pop(0)

        if len(path) > max_depth:
            continue

        neighbors = graph.get_neighbors(current_id)
        for n in neighbors:
            nid = _get_entity_id(n)
            if nid == to_id:
                return path + [nid]
            if nid and nid not in visited:
                visited.add(nid)
                queue.append((nid, path + [nid]))

    return None
