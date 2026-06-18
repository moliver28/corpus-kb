"""Graph storage — abstract interface + SQLite (Level 1) implementation.

The abstract `GraphStore` interface enables drop-in upgrades:
- Level 1: SQLite (ships day one, ~100 lines)
- Level 2: GraphQLite (pip install graphqlite, zero schema changes)
- Level 3: LatticeDB (future, when Python bindings mature)

MCP tools never change — they call the same interface.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from utils.models import Entity, Relation


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class GraphStore(ABC):
    """Abstract graph backend. All MCP graph tools go through this."""

    @abstractmethod
    def add_entity(self, name: str, type: str = "concept",
                   metadata: Optional[dict] = None) -> str:
        """Add an entity. Returns entity_id."""
        ...

    @abstractmethod
    def add_relation(self, source_id: str, target_id: str,
                     rel_type: str = "related_to", weight: float = 1.0,
                     metadata: Optional[dict] = None) -> str:
        """Add a relation between two entities. Returns relation_id."""
        ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get entity by ID."""
        ...

    @abstractmethod
    def get_neighbors(self, entity_id: str, depth: int = 1) -> list[dict]:
        """Get neighboring entities and relations up to `depth` hops."""
        ...

    @abstractmethod
    def search_entities(self, query: str, type: Optional[str] = None) -> list[Entity]:
        """Search entities by name (case-insensitive contains)."""
        ...

    @abstractmethod
    def bfs_traverse(self, start_id: str, max_depth: int = 5) -> list[dict]:
        """BFS traversal from a starting entity."""
        ...

    @abstractmethod
    def get_stats(self) -> dict:
        """Get graph statistics (entity count, relation count)."""
        ...

    # Level 2+ features — NotImplemented by default at Level 1
    def cypher_query(self, query: str) -> list:
        raise NotImplementedError(
            "Cypher queries require GraphQLite (Level 2). "
            "Run: pip install graphqlite"
        )

    def pagerank(self) -> dict:
        raise NotImplementedError(
            "PageRank requires GraphQLite (Level 2). "
            "Run: pip install graphqlite"
        )

    def louvain(self) -> dict:
        raise NotImplementedError(
            "Louvain community detection requires GraphQLite (Level 2). "
            "Run: pip install graphqlite"
        )

    def shortest_path(self, from_id: str, to_id: str) -> list:
        raise NotImplementedError(
            "Shortest path requires GraphQLite (Level 2). "
            "Run: pip install graphqlite"
        )


# ---------------------------------------------------------------------------
# Level 1: SQLite implementation
# ---------------------------------------------------------------------------

class SQLiteGraphStore(GraphStore):
    """SQLite-backed entity-relation graph.

    Tables:
        entities (entity_id, name, type, metadata, created_at)
        relations (relation_id, source_id, target_id, relation_type, weight, metadata, created_at)

    Supports BFS traversal via recursive CTEs.
    """

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).expanduser().resolve())
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    def _ensure_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'concept',
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS relations (
                relation_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES entities(entity_id),
                target_id TEXT NOT NULL REFERENCES entities(entity_id),
                relation_type TEXT NOT NULL DEFAULT 'related_to',
                weight REAL DEFAULT 1.0,
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_relations_source
                ON relations(source_id);
            CREATE INDEX IF NOT EXISTS idx_relations_target
                ON relations(target_id);
            CREATE INDEX IF NOT EXISTS idx_relations_type
                ON relations(relation_type);
            CREATE INDEX IF NOT EXISTS idx_entities_name
                ON entities(name);
        """)
        self.conn.commit()

    def _row_to_entity(self, row: sqlite3.Row) -> Entity:
        return Entity(
            entity_id=row["entity_id"],
            name=row["name"],
            type=row["type"],
            metadata=json.loads(row["metadata"]),
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def add_entity(self, name: str, type: str = "concept",
                   metadata: Optional[dict] = None) -> str:
        entity_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO entities (entity_id, name, type, metadata) VALUES (?, ?, ?, ?)",
            (entity_id, name, type, json.dumps(metadata or {})),
        )
        self.conn.commit()
        return entity_id

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        row = self.conn.execute(
            "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def search_entities(self, query: str, type: Optional[str] = None) -> list[Entity]:
        sql = "SELECT * FROM entities WHERE name LIKE ?"
        params = [f"%{query}%"]
        if type:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY name LIMIT 100"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_entity(r) for r in rows]

    # ------------------------------------------------------------------
    # Relation CRUD
    # ------------------------------------------------------------------

    def add_relation(self, source_id: str, target_id: str,
                     rel_type: str = "related_to", weight: float = 1.0,
                     metadata: Optional[dict] = None) -> str:
        relation_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO relations
               (relation_id, source_id, target_id, relation_type, weight, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (relation_id, source_id, target_id, rel_type, weight,
             json.dumps(metadata or {})),
        )
        self.conn.commit()
        return relation_id

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def get_neighbors(self, entity_id: str, depth: int = 1) -> list[dict]:
        """Get neighboring entities with their relation details."""
        if depth == 1:
            rows = self.conn.execute("""
                SELECT e.*, r.relation_id, r.relation_type, r.weight as rel_weight,
                       CASE WHEN r.source_id = ? THEN 'outgoing' ELSE 'incoming' END as direction
                FROM relations r
                JOIN entities e ON e.entity_id =
                    CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END
                WHERE r.source_id = ? OR r.target_id = ?
            """, (entity_id, entity_id, entity_id, entity_id)).fetchall()
        else:
            rows = self.bfs_traverse(entity_id, max_depth=depth)
            return rows

        results = []
        for row in rows:
            results.append({
                "entity": self._row_to_entity(row),
                "relation": {
                    "relation_id": row["relation_id"],
                    "relation_type": row["relation_type"],
                    "weight": row["rel_weight"],
                    "direction": row["direction"],
                },
            })
        return results

    def bfs_traverse(self, start_id: str, max_depth: int = 5) -> list[dict]:
        """BFS traversal using recursive CTE."""
        rows = self.conn.execute("""
            WITH RECURSIVE traverse AS (
                -- Anchor: start node
                SELECT
                    e.entity_id, e.name, e.type, e.metadata, e.created_at,
                    r.relation_id, r.relation_type, r.weight as rel_weight,
                    CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END as neighbor_id,
                    CAST(? AS TEXT) as source_entity_id,
                    0 as depth,
                    CASE WHEN r.source_id = ? THEN 'outgoing' ELSE 'incoming' END as direction
                FROM entities e
                LEFT JOIN relations r ON r.source_id = e.entity_id OR r.target_id = e.entity_id
                WHERE e.entity_id = ?

                UNION ALL

                -- Recursive: expand neighbors
                SELECT
                    e.entity_id, e.name, e.type, e.metadata, e.created_at,
                    r.relation_id, r.relation_type, r.weight as rel_weight,
                    CASE WHEN r.source_id = e.entity_id THEN r.target_id ELSE r.source_id END,
                    e.entity_id as source_entity_id,
                    t.depth + 1,
                    CASE WHEN r.source_id = e.entity_id THEN 'outgoing' ELSE 'incoming' END
                FROM traverse t
                JOIN relations r ON r.source_id = t.entity_id OR r.target_id = t.entity_id
                JOIN entities e ON e.entity_id =
                    CASE WHEN r.source_id = t.entity_id THEN r.target_id ELSE r.source_id END
                WHERE t.depth < ?
                  AND e.entity_id != t.source_entity_id  -- prevent immediate backtrack
            )
            SELECT DISTINCT * FROM traverse ORDER BY depth, name
        """, (start_id, start_id, start_id, start_id, max_depth)).fetchall()

        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        entity_count = self.conn.execute(
            "SELECT COUNT(*) FROM entities"
        ).fetchone()[0]
        relation_count = self.conn.execute(
            "SELECT COUNT(*) FROM relations"
        ).fetchone()[0]
        return {
            "total_entities": entity_count,
            "total_relations": relation_count,
            "backend": "sqlite",
            "db_path": self.db_path,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_graph_store(config: dict) -> GraphStore:
    """Factory: creates the appropriate GraphStore based on config.

    Config format:
        graph:
            backend: sqlite           # sqlite | graphqlite | latticedb
            path: ~/.corpus-kb/graph.db
    """
    backend = config.get("graph", {}).get("backend", "sqlite")
    db_path = config.get("storage", {}).get("graph_db", "~/.corpus-kb/graph.db")

    if backend == "sqlite":
        return SQLiteGraphStore(db_path)
    elif backend == "graphqlite":
        return _create_graphqlite_store(db_path)
    elif backend == "latticedb":
        raise NotImplementedError(
            "LatticeDB support is not yet available. "
            "Use 'sqlite' (Level 1) or 'graphqlite' (Level 2) instead."
        )
    else:
        raise ValueError(f"Unknown graph backend: {backend}")


def _create_graphqlite_store(db_path: str) -> GraphStore:
    """Create a GraphQLite-backed store (Level 2)."""
    try:
        from graphqlite import Graph
    except ImportError:
        raise ImportError(
            "GraphQLite is not installed. Run: pip install graphqlite"
        )

    # Dynamically create a GraphQLiteGraphStore class that wraps GraphQLite
    # This keeps the import optional — no pip dependency at Level 1
    class GraphQLiteGraphStore(GraphStore):
        """GraphQLite-backed graph store — full Cypher + algorithms."""

        def __init__(self, db_path: str):
            resolved = str(Path(db_path).expanduser().resolve())
            self.g = Graph(resolved)
            self._resolved = resolved

        def add_entity(self, name: str, type: str = "concept",
                       metadata: Optional[dict] = None) -> str:
            entity_id = str(uuid.uuid4())
            self.g.upsert_node(entity_id, {
                "name": name, "type": type, **(metadata or {}),
            })
            return entity_id

        def add_relation(self, source_id: str, target_id: str,
                         rel_type: str = "related_to", weight: float = 1.0,
                         metadata: Optional[dict] = None) -> str:
            self.g.upsert_edge(
                source_id, target_id,
                {"weight": weight, **(metadata or {})},
                rel_type=rel_type,
            )
            return str(uuid.uuid4())

        def get_entity(self, entity_id: str) -> Optional[Entity]:
            results = self.g.query(
                f"MATCH (n) WHERE n.id = '{entity_id}' RETURN n"
            )
            if not results:
                return None
            n = results[0]["n"]
            return Entity(
                entity_id=n.get("id", entity_id),
                name=n.get("name", ""),
                type=n.get("type", "concept"),
                metadata={k: v for k, v in n.items() if k not in ("id", "name", "type")},
            )

        def get_neighbors(self, entity_id: str, depth: int = 1) -> list[dict]:
            results = self.g.query(f"""
                MATCH (n)-[r]-(m) WHERE n.id = '{entity_id}'
                RETURN m.id as entity_id, m.name as name,
                       type(r) as relation_type, r.weight as weight
            """)
            return [dict(r) for r in results]

        def search_entities(self, query: str,
                            type: Optional[str] = None) -> list[Entity]:
            type_filter = f" AND n.type = '{type}'" if type else ""
            results = self.g.query(f"""
                MATCH (n) WHERE n.name CONTAINS '{query}'{type_filter}
                RETURN n.id, n.name, n.type
            """)
            return [
                Entity(entity_id=r["n.id"], name=r["n.name"], type=r.get("n.type", "concept"))
                for r in results
            ]

        def bfs_traverse(self, start_id: str, max_depth: int = 5) -> list[dict]:
            results = self.g.query(f"""
                MATCH (n)-[r*1..{max_depth}]-(m)
                WHERE n.id = '{start_id}'
                RETURN m.id, m.name, length(r) as depth
            """)
            return [dict(r) for r in results]

        def cypher_query(self, query: str) -> list:
            return self.g.query(query)

        def pagerank(self) -> dict:
            return self.g.pagerank()

        def louvain(self) -> dict:
            return self.g.louvain()

        def shortest_path(self, from_id: str, to_id: str) -> list:
            return self.g.dijkstra(from_id, to_id)

        def get_stats(self) -> dict:
            node_count = len(self.g.query("MATCH (n) RETURN count(n) as count"))
            edge_count = len(self.g.query("MATCH ()-[r]->() RETURN count(r) as count"))
            return {
                "total_entities": node_count,
                "total_relations": edge_count,
                "backend": "graphqlite",
                "db_path": self._resolved,
            }

    return GraphQLiteGraphStore(db_path)
