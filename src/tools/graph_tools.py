"""MCP tools for entity graph operations.

Tools:
- add_entity: Add a knowledge graph entity
- add_relation: Add a relation between entities
- search_graph: Search entities by name/type
- bfs_traversal: BFS from a starting entity
"""

from __future__ import annotations

from typing import Optional

from storage.graph_store import GraphStore


def register_tools(mcp, graph: GraphStore):
    """Register all graph tools with the MCP server."""

    @mcp.tool()
    def add_entity(name: str, type: str = "concept",
                   metadata: Optional[dict] = None) -> dict:
        """Add an entity to the knowledge graph.

        Args:
            name: Entity name (e.g., "MyClass", "Authentication", "Paris").
            type: Entity type (class, function, concept, person, place, etc.).
            metadata: Optional metadata dict.

        Returns:
            Created entity details including entity_id.
        """
        entity_id = graph.add_entity(
            name=name,
            type=type,
            metadata=metadata or {},
        )
        return {"entity_id": entity_id, "name": name, "type": type}

    @mcp.tool()
    def add_relation(source_id: str, target_id: str,
                     rel_type: str = "related_to",
                     weight: float = 1.0) -> dict:
        """Add a directed relation between two entities.

        Args:
            source_id: Source entity ID.
            target_id: Target entity ID.
            rel_type: Type of relation (CALLS, DEPENDS_ON, CONTAINS, etc.).
            weight: Relation strength (0.0 to 1.0).

        Returns:
            Created relation details.
        """
        relation_id = graph.add_relation(
            source_id=source_id,
            target_id=target_id,
            rel_type=rel_type,
            weight=weight,
        )
        return {"relation_id": relation_id,
                "source_id": source_id,
                "target_id": target_id,
                "type": rel_type}

    @mcp.tool()
    def search_graph(query: str, type: Optional[str] = None,
                     limit: int = 20) -> list[dict]:
        """Search entities in the knowledge graph by name or type.

        Args:
            query: Search term for entity name.
            type: Optional entity type filter.
            limit: Max results (max 100).

        Returns:
            List of matching entities.
        """
        limit = min(limit, 100)
        results = graph.search_entities(query, type=type)
        results = results[:limit]
        return [
            {
                "entity_id": e.entity_id,
                "name": e.name,
                "type": e.type,
                "metadata": e.metadata,
            }
            for e in results
        ]

    @mcp.tool()
    def bfs(start_entity_id: str, max_depth: int = 3) -> list[dict]:
        """BFS traversal from a starting entity.

        Args:
            start_entity_id: Entity ID to start from.
            max_depth: Max traversal depth (1-10).

        Returns:
            List of (entity_id, name, type, depth) entries.
        """
        max_depth = max(1, min(max_depth, 10))
        results = graph.bfs_traverse(start_entity_id, max_depth=max_depth)
        return [
            {
                "entity_id": r.get("entity_id", ""),
                "name": r.get("name", ""),
                "type": r.get("type", ""),
                "depth": r.get("depth", 0),
                "relations": r.get("relations", []),
            }
            for r in results
        ]

    @mcp.tool()
    def get_entity_relations(entity_id: str) -> list[dict]:
        """Get all relations for an entity (incoming and outgoing).

        Args:
            entity_id: Entity ID.

        Returns:
            List of relations with source/target info.
        """
        # Use neighbors with depth=1 to get relations
        neighbors = graph.get_neighbors(entity_id, depth=1)
        relations = []
        seen = set()
        for n in neighbors:
            for rel in n.get("relations", []):
                rel_id = rel.get("relation_id", "")
                if rel_id and rel_id not in seen:
                    seen.add(rel_id)
                    relations.append({
                        "relation_id": rel_id,
                        "source_id": rel.get("source_id", ""),
                        "target_id": rel.get("target_id", ""),
                        "type": rel.get("rel_type", ""),
                        "weight": rel.get("weight", 1.0),
                    })
        return relations
