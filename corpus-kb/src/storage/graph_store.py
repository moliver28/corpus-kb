"""Abstract GraphStore interface and SQLite implementation.

The GraphStore is the pattern for swappable graph backends:
- Level 1: SQLite (ships day one, zero deps)
- Level 2: GraphQLite (pip install, same .db file, Cypher + algorithms)
- Level 3a: Apache AGE (PostgreSQL extension, production openCypher)
- Level 3b: LatticeDB (when Python bindings mature, single-file graph+vector+FTS)

MCP tools never change; only the backend swaps.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from src.utils.models import Entity, Relation


# ============================================================================
# Abstract Interface
# ============================================================================


class GraphStore(ABC):
    """Abstract interface for graph storage backends."""

    @abstractmethod
    def add_entity(self, entity: Entity) -> str:
        """Add an entity to the graph.

        Args:
            entity: Entity object with name, type, source_type, metadata.

        Returns:
            The entity_id (UUID string).
        """
        pass

    @abstractmethod
    def add_relation(self, relation: Relation) -> str:
        """Add a relation between two entities.

        Args:
            relation: Relation object with source/target entity IDs, type, metadata.

        Returns:
            The relation_id (UUID string).
        """
        pass

    @abstractmethod
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        pass

    @abstractmethod
    def search_entities(self, name: str, entity_type: Optional[str] = None) -> list[Entity]:
        """Search entities by name and optional type."""
        pass

    @abstractmethod
    def get_entity_relations(self, entity_id: str) -> list[Relation]:
        """Get all relations for an entity."""
        pass

    @abstractmethod
    def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict:
        """Breadth-first search from a starting entity."""
        pass


# ============================================================================
# SQLite Implementation (Level 1)
# ============================================================================


class SQLiteGraphStore(GraphStore):
    """SQLite-backed graph store (Level 1)."""

    def __init__(self, db_path: str | Path):
        """Initialize SQLite graph store.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_document_id TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relations (
                    relation_id TEXT PRIMARY KEY,
                    source_entity_id TEXT NOT NULL,
                    target_entity_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_entity_id) REFERENCES entities(entity_id),
                    FOREIGN KEY (target_entity_id) REFERENCES entities(entity_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_entity_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_entity_id)"
            )
            conn.commit()

    def add_entity(self, entity: Entity) -> str:
        """Add an entity to the graph."""
        import json

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO entities
                (entity_id, name, entity_type, source_type, source_document_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.entity_id,
                    entity.name,
                    entity.entity_type,
                    entity.source_type,
                    entity.source_document_id,
                    json.dumps(entity.metadata),
                ),
            )
            conn.commit()
        return entity.entity_id

    def add_relation(self, relation: Relation) -> str:
        """Add a relation between two entities."""
        import json

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO relations
                (relation_id, source_entity_id, target_entity_id, relation_type, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    relation.relation_id,
                    relation.source_entity_id,
                    relation.target_entity_id,
                    relation.relation_type,
                    json.dumps(relation.metadata),
                ),
            )
            conn.commit()
        return relation.relation_id

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        import json

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
            ).fetchone()

        if not row:
            return None

        return Entity(
            entity_id=row["entity_id"],
            name=row["name"],
            entity_type=row["entity_type"],
            source_type=row["source_type"],
            source_document_id=row["source_document_id"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def search_entities(self, name: str, entity_type: Optional[str] = None) -> list[Entity]:
        """Search entities by name and optional type."""
        import json

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if entity_type:
                rows = conn.execute(
                    "SELECT * FROM entities WHERE name LIKE ? AND entity_type = ?",
                    (f"%{name}%", entity_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entities WHERE name LIKE ?", (f"%{name}%",)
                ).fetchall()

        entities = []
        for row in rows:
            entities.append(
                Entity(
                    entity_id=row["entity_id"],
                    name=row["name"],
                    entity_type=row["entity_type"],
                    source_type=row["source_type"],
                    source_document_id=row["source_document_id"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
            )
        return entities

    def get_entity_relations(self, entity_id: str) -> list[Relation]:
        """Get all relations for an entity."""
        import json

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM relations
                WHERE source_entity_id = ? OR target_entity_id = ?
                """,
                (entity_id, entity_id),
            ).fetchall()

        relations = []
        for row in rows:
            relations.append(
                Relation(
                    relation_id=row["relation_id"],
                    source_entity_id=row["source_entity_id"],
                    target_entity_id=row["target_entity_id"],
                    relation_type=row["relation_type"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
            )
        return relations

    def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict:
        """Breadth-first search from a starting entity."""
        from collections import deque

        visited = set()
        queue = deque([(start_entity_id, 0)])
        result = {"start": start_entity_id, "nodes": {}, "edges": []}

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            while queue:
                entity_id, depth = queue.popleft()

                if entity_id in visited or depth > max_depth:
                    continue

                visited.add(entity_id)

                # Get entity
                entity_row = conn.execute(
                    "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
                ).fetchone()

                if entity_row:
                    result["nodes"][entity_id] = {
                        "name": entity_row["name"],
                        "type": entity_row["entity_type"],
                        "depth": depth,
                    }

                    # Get relations
                    relation_rows = conn.execute(
                        """
                        SELECT * FROM relations
                        WHERE source_entity_id = ? OR target_entity_id = ?
                        """,
                        (entity_id, entity_id),
                    ).fetchall()

                    for rel_row in relation_rows:
                        result["edges"].append(
                            {
                                "source": rel_row["source_entity_id"],
                                "target": rel_row["target_entity_id"],
                                "type": rel_row["relation_type"],
                            }
                        )

                        # Queue neighbors
                        next_id = (
                            rel_row["target_entity_id"]
                            if rel_row["source_entity_id"] == entity_id
                            else rel_row["source_entity_id"]
                        )
                        if next_id not in visited:
                            queue.append((next_id, depth + 1))

        return result


# ============================================================================
# Factory
# ============================================================================


def create_graph_store(backend: str, db_path: str | Path) -> GraphStore:
    """Create a graph store instance.

    Args:
        backend: "sqlite" | "graphqlite" | "latticedb"
        db_path: Path to database file.

    Returns:
        GraphStore instance.

    Raises:
        ValueError: If backend is not supported.
    """
    if backend == "sqlite":
        return SQLiteGraphStore(db_path)
    elif backend == "graphqlite":
        try:
            from src.storage.graphqlite_store import GraphQLiteStore

            return GraphQLiteStore(db_path)
        except ImportError:
            raise ValueError(
                "GraphQLite backend requires: pip install graphqlite"
            ) from None
    else:
        raise ValueError(f"Unsupported graph backend: {backend}")
