"""Abstract GraphStore interface and SQLite implementation.

The GraphStore is the pattern for swappable graph backends:
- Level 1: SQLite (ships day one, zero deps)
- Level 2: GraphQLite (pip install, same .db file, Cypher + algorithms)
- Level 3a: Apache AGE (PostgreSQL extension, production openCypher)
- Level 3b: LatticeDB (when Python bindings mature, single-file graph+vector+FTS)

MCP tools never change; only the backend swaps.
"""

from __future__ import annotations

import importlib
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Optional, cast

from ..utils.models import Chunk, Document, Entity, Relation


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

    def batch_add_entities(self, entities: list[Entity]) -> list[str]:
        """Add multiple entities to the graph.

        Args:
            entities: List of Entity objects.

        Returns:
            List of entity_id strings.
        """
        return [self.add_entity(entity) for entity in entities]

    def batch_add_relations(self, relations: list[Relation]) -> list[str]:
        """Add multiple relations to the graph.

        Args:
            relations: List of Relation objects.

        Returns:
            List of relation_id strings.
        """
        return [self.add_relation(relation) for relation in relations]

    @abstractmethod
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        pass

    @abstractmethod
    def search_entities(
        self, name: str, entity_type: Optional[str] = None
    ) -> list[Entity]:
        """Search entities by name and optional type."""
        pass

    @abstractmethod
    def get_entity_relations(self, entity_id: str) -> list[Relation]:
        """Get all relations for an entity."""
        pass

    @abstractmethod
    def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict[str, object]:
        """Breadth-first search from a starting entity."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the graph store and release resources."""
        pass

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """Return a context manager that wraps graph writes in a transaction."""
        pass

    def add_document(self, document: Document) -> str:
        """Persist a Document record.

        Backends that support provenance tables should override this method.
        The default raises NotImplementedError.
        """
        raise NotImplementedError

    def add_chunk(self, chunk: Chunk) -> str:
        """Persist a Chunk record.

        Backends that support provenance tables should override this method.
        The default raises NotImplementedError.
        """
        raise NotImplementedError


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
        self.db_path = Path(db_path) if not isinstance(db_path, Path) else db_path
        self._persistent_conn: Optional[sqlite3.Connection] = None
        self._txn_conn: Optional[sqlite3.Connection] = None

        # For in-memory databases, use shared cache mode and keep a persistent
        # connection so the database survives between per-operation connections.
        if str(self.db_path) == ":memory:":
            self.db_uri = "file::memory:?cache=shared"
            self._use_uri = True
            self._persistent_conn = sqlite3.connect(self.db_uri, uri=True)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_uri = str(self.db_path)
            self._use_uri = False

        self._init_schema()

    def _open_connection(self) -> sqlite3.Connection:
        """Open a new database connection with PRAGMA foreign_keys=ON.

        Enables ``PRAGMA foreign_keys=ON`` on every connection because SQLite
        disables foreign-key enforcement by default and resets it per
        connection. Application-level validation in ``batch_add_entities``
        and ``_validate_chunk_ids`` handles the ``chunk_id`` / ``document_id``
        references because those columns are nullable.
        """
        if self._use_uri:
            conn = sqlite3.connect(self.db_uri, uri=True)
        else:
            conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a connection for a single operation.

        Inside a transaction, yields the shared transaction connection
        without committing or closing. Outside a transaction, opens a
        new connection, commits on success, rolls back on exception,
        and closes.
        """
        if self._txn_conn is not None:
            yield self._txn_conn
        else:
            conn = self._open_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with self._conn() as conn:
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
            self._migrate_provenance_columns()
            self._init_provenance_tables()

    def _init_provenance_tables(self) -> None:
        """Create idempotent documents/chunks provenance tables."""
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    path TEXT,
                    source_type TEXT NOT NULL,
                    content TEXT,
                    size_bytes INTEGER,
                    chunk_count INTEGER DEFAULT 0,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    text TEXT,
                    source_type TEXT NOT NULL,
                    source_start_char INTEGER,
                    source_end_char INTEGER,
                    start_line INTEGER,
                    end_line INTEGER,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)"
            )

    def _migrate_provenance_columns(self) -> None:
        """Idempotently add provenance columns to entities and relations tables."""
        provenance_columns = {
            "chunk_id": "TEXT",
            "source_start_char": "INTEGER",
            "source_end_char": "INTEGER",
            "confidence": "REAL",
            "extractor_id": "TEXT",
        }
        with self._conn() as conn:
            for table in ("entities", "relations"):
                existing = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for column, col_type in provenance_columns.items():
                    if column not in existing:
                        conn.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                        )

    def add_entity(self, entity: Entity) -> str:
        """Add an entity to the graph.

        Raises:
            ValueError: If ``entity.chunk_id`` references a non-existent chunk.
        """
        import json

        with self._conn() as conn:
            if entity.chunk_id is not None:
                self._validate_chunk_ids(conn, {entity.chunk_id})
            conn.execute(
                """
                INSERT OR REPLACE INTO entities
                (entity_id, name, entity_type, source_type, source_document_id, metadata,
                 chunk_id, source_start_char, source_end_char, confidence, extractor_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.entity_id,
                    entity.name,
                    entity.entity_type,
                    entity.source_type,
                    entity.source_document_id,
                    json.dumps(entity.metadata),
                    entity.chunk_id,
                    entity.source_start_char,
                    entity.source_end_char,
                    entity.confidence,
                    entity.extractor_id,
                ),
            )
        return entity.entity_id

    def add_relation(self, relation: Relation) -> str:
        """Add a relation between two entities."""
        import json

        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO relations
                (relation_id, source_entity_id, target_entity_id, relation_type, metadata,
                 chunk_id, source_start_char, source_end_char, confidence, extractor_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation.relation_id,
                    relation.source_entity_id,
                    relation.target_entity_id,
                    relation.relation_type,
                    json.dumps(relation.metadata),
                    relation.chunk_id,
                    relation.source_start_char,
                    relation.source_end_char,
                    relation.confidence,
                    relation.extractor_id,
                ),
            )
        return relation.relation_id

    def _validate_chunk_ids(
        self, conn: sqlite3.Connection, chunk_ids: set[str]
    ) -> None:
        """Raise ValueError if any referenced chunk_id does not exist."""
        if not chunk_ids:
            return
        placeholders = ", ".join("?" for _ in chunk_ids)
        existing = {
            row[0]
            for row in conn.execute(
                f"SELECT chunk_id FROM chunks WHERE chunk_id IN ({placeholders})",
                tuple(chunk_ids),
            ).fetchall()
        }
        missing = chunk_ids - existing
        if missing:
            raise ValueError(f"Chunk IDs not found: {sorted(missing)}")

    def batch_add_entities(self, entities: list[Entity]) -> list[str]:
        """Add multiple entities to the SQLite graph in one statement."""
        import json

        if not entities:
            return []

        with self._conn() as conn:
            chunk_ids = {
                entity.chunk_id for entity in entities if entity.chunk_id is not None
            }
            self._validate_chunk_ids(conn, chunk_ids)
            conn.executemany(
                """
                INSERT OR REPLACE INTO entities
                (entity_id, name, entity_type, source_type, source_document_id, metadata,
                 chunk_id, source_start_char, source_end_char, confidence, extractor_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        entity.entity_id,
                        entity.name,
                        entity.entity_type,
                        entity.source_type,
                        entity.source_document_id,
                        json.dumps(entity.metadata),
                        entity.chunk_id,
                        entity.source_start_char,
                        entity.source_end_char,
                        entity.confidence,
                        entity.extractor_id,
                    )
                    for entity in entities
                ],
            )
        return [entity.entity_id for entity in entities]

    def batch_add_relations(self, relations: list[Relation]) -> list[str]:
        """Add multiple relations to the SQLite graph in one statement."""
        import json

        if not relations:
            return []

        with self._conn() as conn:
            chunk_ids = {
                relation.chunk_id
                for relation in relations
                if relation.chunk_id is not None
            }
            self._validate_chunk_ids(conn, chunk_ids)
            conn.executemany(
                """
                INSERT OR REPLACE INTO relations
                (relation_id, source_entity_id, target_entity_id, relation_type, metadata,
                 chunk_id, source_start_char, source_end_char, confidence, extractor_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        relation.relation_id,
                        relation.source_entity_id,
                        relation.target_entity_id,
                        relation.relation_type,
                        json.dumps(relation.metadata),
                        relation.chunk_id,
                        relation.source_start_char,
                        relation.source_end_char,
                        relation.confidence,
                        relation.extractor_id,
                    )
                    for relation in relations
                ],
            )
        return [relation.relation_id for relation in relations]

    def add_document(self, document: Document) -> str:
        """Persist a Document record."""
        import json

        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO documents
                (document_id, path, source_type, content, size_bytes, chunk_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.document_id,
                    document.path,
                    document.source_type,
                    document.content,
                    document.size_bytes,
                    document.chunk_count,
                    json.dumps(document.metadata),
                ),
            )
        return document.document_id

    def add_chunk(self, chunk: Chunk) -> str:
        """Persist a Chunk record."""
        import json

        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO chunks
                (chunk_id, document_id, text, source_type,
                 source_start_char, source_end_char, start_line, end_line, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.text,
                    chunk.source_type,
                    chunk.source_start_char,
                    chunk.source_end_char,
                    chunk.start_line,
                    chunk.end_line,
                    json.dumps(chunk.metadata),
                ),
            )
        return chunk.chunk_id

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        import json

        with self._conn() as conn:
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
            chunk_id=row["chunk_id"],
            source_start_char=row["source_start_char"],
            source_end_char=row["source_end_char"],
            confidence=row["confidence"],
            extractor_id=row["extractor_id"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def search_entities(
        self, name: str, entity_type: Optional[str] = None
    ) -> list[Entity]:
        """Search entities by name and optional type."""
        import json

        with self._conn() as conn:
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

        entities: list[Entity] = []
        for row in rows:
            entities.append(
                Entity(
                    entity_id=row["entity_id"],
                    name=row["name"],
                    entity_type=row["entity_type"],
                    source_type=row["source_type"],
                    source_document_id=row["source_document_id"],
                    chunk_id=row["chunk_id"],
                    source_start_char=row["source_start_char"],
                    source_end_char=row["source_end_char"],
                    confidence=row["confidence"],
                    extractor_id=row["extractor_id"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
            )
        return entities

    def get_entity_relations(self, entity_id: str) -> list[Relation]:
        """Get all relations for an entity."""
        import json

        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM relations
                WHERE source_entity_id = ? OR target_entity_id = ?
                """,
                (entity_id, entity_id),
            ).fetchall()

        relations: list[Relation] = []
        for row in rows:
            relations.append(
                Relation(
                    relation_id=row["relation_id"],
                    source_entity_id=row["source_entity_id"],
                    target_entity_id=row["target_entity_id"],
                    relation_type=row["relation_type"],
                    chunk_id=row["chunk_id"],
                    source_start_char=row["source_start_char"],
                    source_end_char=row["source_end_char"],
                    confidence=row["confidence"],
                    extractor_id=row["extractor_id"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
            )
        return relations

    def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict[str, object]:
        """Breadth-first search from a starting entity."""
        from collections import deque

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start_entity_id, 0)])
        nodes: dict[str, object] = {}
        edges: list[object] = []

        with self._conn() as conn:
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
                    nodes[entity_id] = {
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
                        edges.append(
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

        return {"start": start_entity_id, "nodes": nodes, "edges": edges}

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """Context manager wrapping graph writes in a SQLite transaction.

        All write methods called inside this context reuse the same
        connection so their writes are atomic. On exception, all
        uncommitted writes are rolled back.

        Raises:
            RuntimeError: If called inside an already-active transaction.
        """
        if self._txn_conn is not None:
            raise RuntimeError("Nested transactions are not supported")
        conn = self._open_connection()
        conn.execute("PRAGMA foreign_keys=ON")
        self._txn_conn = conn
        conn.execute("BEGIN")
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._txn_conn = None
            conn.close()

    def close(self) -> None:
        """Close the graph store and release resources.

        For SQLite, this closes the optional persistent in-memory connection.
        """
        if self._persistent_conn is not None:
            self._persistent_conn.close()
            self._persistent_conn = None


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
            module = importlib.import_module("src.storage.graphqlite_store")
            cls = getattr(module, "GraphQLiteStore")
            return cast(GraphStore, cls(db_path))
        except ImportError:
            raise ValueError(
                "GraphQLite backend requires: pip install graphqlite"
            ) from None
    else:
        raise ValueError(f"Unsupported graph backend: {backend}")
