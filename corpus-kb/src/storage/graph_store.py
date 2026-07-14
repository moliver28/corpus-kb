"""Abstract GraphStore interface and Postgres implementation.

The GraphStore is the pattern for swappable graph backends:
- Level 1: Postgres (default, asyncpg + RLS)
- Level 2: Apache AGE (PostgreSQL extension, production openCypher)

MCP tools never change; only the backend swaps.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg

from src.utils.models import Chunk, Document, Entity, Relation

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


# ============================================================================
# Abstract Interface
# ============================================================================


class GraphStore(ABC):
    """Abstract interface for graph storage backends.

    All methods are async — backends use asyncpg for Postgres access.
    """

    @abstractmethod
    async def add_entity(self, entity: Entity) -> str:
        """Add an entity to the graph.

        Args:
            entity: Entity object with name, type, source_type, metadata.

        Returns:
            The entity_id (UUID string).
        """
        pass

    @abstractmethod
    async def add_relation(self, relation: Relation) -> str:
        """Add a relation between two entities.

        Args:
            relation: Relation object with source/target entity IDs, type, metadata.

        Returns:
            The relation_id (UUID string).
        """
        pass

    async def batch_add_entities(self, entities: list[Entity]) -> list[str]:
        """Add multiple entities to the graph.

        Args:
            entities: List of Entity objects.

        Returns:
            List of entity_id strings.
        """
        result: list[str] = []
        for entity in entities:
            eid = await self.add_entity(entity)
            result.append(eid)
        return result

    async def batch_add_relations(self, relations: list[Relation]) -> list[str]:
        """Add multiple relations to the graph.

        Args:
            relations: List of Relation objects.

        Returns:
            List of relation_id strings.
        """
        result: list[str] = []
        for relation in relations:
            rid = await self.add_relation(relation)
            result.append(rid)
        return result

    @abstractmethod
    async def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        pass

    @abstractmethod
    async def search_entities(
        self, name: str, entity_type: Optional[str] = None
    ) -> list[Entity]:
        """Search entities by name and optional type."""
        pass

    @abstractmethod
    async def get_entity_relations(self, entity_id: str) -> list[Relation]:
        """Get all relations for an entity."""
        pass

    @abstractmethod
    async def bfs(
        self, start_entity_id: str, max_depth: int = 5
    ) -> dict[str, object]:
        """Breadth-first search from a starting entity."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the graph store and release resources."""
        pass

    @abstractmethod
    def transaction(self):
        """Return an async context manager that wraps graph writes in a transaction."""
        pass

    async def add_document(self, document: Document) -> str:
        """Persist a Document record.

        Backends that support provenance tables should override this method.
        The default raises NotImplementedError.
        """
        raise NotImplementedError

    async def add_chunk(self, chunk: Chunk) -> str:
        """Persist a Chunk record.

        Backends that support provenance tables should override this method.
        The default raises NotImplementedError.
        """
        raise NotImplementedError


# ============================================================================
# Postgres Implementation (Level 1)
# ============================================================================


class PostgresGraphStore(GraphStore):
    """Postgres-backed graph store using asyncpg.

    All methods are async and acquire a connection from the pool,
    set the tenant context via SET LOCAL, then execute parameterized SQL.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self._pool = pool
        self._tenant_id = tenant_id
        self._conn: Optional[asyncpg.Connection] = None

    async def _get_conn(self) -> asyncpg.Connection:
        """Return the current transaction connection, or acquire one from the pool."""
        if self._conn is not None:
            return self._conn
        conn = await self._pool.acquire()
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)",
            self._tenant_id,
        )
        return conn

    async def _release_conn(self, conn: asyncpg.Connection) -> None:
        """Release a connection back to the pool if it was acquired (not in a transaction)."""
        if self._conn is None and conn is not None:
            await self._pool.release(conn)

    async def add_entity(self, entity: Entity) -> str:
        """Insert an entity into the entities table."""
        conn = await self._get_conn()
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO entities (entity_id, tenant_id, name, entity_type,
                    source_document_id, metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
                RETURNING entity_id::text
                """,
                entity.entity_id,
                self._tenant_id,
                entity.name,
                entity.entity_type,
                entity.source_document_id,
                json.dumps(entity.metadata),
            )
            if row:
                return str(row["entity_id"])
            # Entity already exists — fetch its ID
            row = await conn.fetchrow(
                """
                SELECT entity_id::text FROM entities
                WHERE tenant_id = $1 AND name = $2 AND entity_type = $3
                """,
                self._tenant_id,
                entity.name,
                entity.entity_type,
            )
            return str(row["entity_id"]) if row else entity.entity_id
        finally:
            await self._release_conn(conn)

    async def add_relation(self, relation: Relation) -> str:
        """Insert a relation into the relations table."""
        conn = await self._get_conn()
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO relations (relation_id, tenant_id, source_entity_id,
                    target_entity_id, relation_type, weight, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (tenant_id, source_entity_id, target_entity_id, relation_type)
                DO NOTHING
                RETURNING relation_id::text
                """,
                relation.relation_id,
                self._tenant_id,
                relation.source_entity_id,
                relation.target_entity_id,
                relation.relation_type,
                relation.weight if relation.weight else 1.0,
                json.dumps(relation.metadata),
            )
            if row:
                return str(row["relation_id"])
            row = await conn.fetchrow(
                """
                SELECT relation_id::text FROM relations
                WHERE tenant_id = $1 AND source_entity_id = $2
                  AND target_entity_id = $3 AND relation_type = $4
                """,
                self._tenant_id,
                relation.source_entity_id,
                relation.target_entity_id,
                relation.relation_type,
            )
            return str(row["relation_id"]) if row else relation.relation_id
        finally:
            await self._release_conn(conn)

    async def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Fetch an entity by ID."""
        conn = await self._get_conn()
        try:
            row = await conn.fetchrow(
                """
                SELECT entity_id::text, name, entity_type, metadata::text,
                       source_document_id::text
                FROM entities WHERE entity_id = $1 AND tenant_id = $2
                """,
                entity_id,
                self._tenant_id,
            )
            if not row:
                return None
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            return Entity(
                entity_id=row["entity_id"],
                name=row["name"],
                entity_type=row["entity_type"],
                source_type="code",
                source_document_id=row["source_document_id"],
                metadata=metadata,
            )
        finally:
            await self._release_conn(conn)

    async def search_entities(
        self, name: str, entity_type: Optional[str] = None
    ) -> list[Entity]:
        """Search entities by name (LIKE) and optional type filter."""
        conn = await self._get_conn()
        try:
            pattern = f"%{name}%"
            if entity_type:
                rows = await conn.fetch(
                    """
                    SELECT entity_id::text, name, entity_type, metadata::text,
                           source_document_id::text
                    FROM entities
                    WHERE tenant_id = $1 AND name ILIKE $2 AND entity_type = $3
                    ORDER BY name ASC
                    """,
                    self._tenant_id,
                    pattern,
                    entity_type,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT entity_id::text, name, entity_type, metadata::text,
                           source_document_id::text
                    FROM entities
                    WHERE tenant_id = $1 AND name ILIKE $2
                    ORDER BY name ASC
                    """,
                    self._tenant_id,
                    pattern,
                )
            result: list[Entity] = []
            for row in rows:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                result.append(
                    Entity(
                        entity_id=row["entity_id"],
                        name=row["name"],
                        entity_type=row["entity_type"],
                        source_type="code",
                        source_document_id=row["source_document_id"],
                        metadata=metadata,
                    )
                )
            return result
        finally:
            await self._release_conn(conn)

    async def get_entity_relations(self, entity_id: str) -> list[Relation]:
        """Get all relations where the entity is source or target."""
        conn = await self._get_conn()
        try:
            rows = await conn.fetch(
                """
                SELECT relation_id::text, source_entity_id::text,
                       target_entity_id::text, relation_type, weight,
                       metadata::text
                FROM relations
                WHERE tenant_id = $1
                  AND (source_entity_id = $2 OR target_entity_id = $2)
                """,
                self._tenant_id,
                entity_id,
            )
            result: list[Relation] = []
            for row in rows:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                result.append(
                    Relation(
                        relation_id=row["relation_id"],
                        source_entity_id=row["source_entity_id"],
                        target_entity_id=row["target_entity_id"],
                        relation_type=row["relation_type"],
                        weight=row["weight"],
                        metadata=metadata,
                    )
                )
            return result
        finally:
            await self._release_conn(conn)

    async def bfs(
        self, start_entity_id: str, max_depth: int = 5
    ) -> dict[str, object]:
        """Breadth-first search using a recursive CTE."""
        conn = await self._get_conn()
        try:
            rows = await conn.fetch(
                """
                WITH RECURSIVE graph_bfs AS (
                    SELECT entity_id::text AS eid, 0 AS depth
                    FROM entities WHERE entity_id = $1 AND tenant_id = $2
                    UNION ALL
                    SELECT
                        CASE
                            WHEN r.source_entity_id = g.eid THEN r.target_entity_id::text
                            ELSE r.source_entity_id::text
                        END AS eid,
                        g.depth + 1
                    FROM graph_bfs g
                    JOIN relations r ON r.tenant_id = $2
                        AND (r.source_entity_id = g.eid::uuid
                             OR r.target_entity_id = g.eid::uuid)
                    WHERE g.depth < $3
                )
                SELECT eid, depth FROM graph_bfs
                """,
                start_entity_id,
                self._tenant_id,
                max_depth,
            )
            visited: dict[str, int] = {}
            for row in rows:
                eid = row["eid"]
                d = row["depth"]
                if eid not in visited or d < visited[eid]:
                    visited[eid] = d
            return {
                "start_entity_id": start_entity_id,
                "max_depth": max_depth,
                "visited": visited,
            }
        finally:
            await self._release_conn(conn)

    async def add_document(self, document: Document) -> str:
        """Insert a document into the documents table."""
        conn = await self._get_conn()
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO documents (doc_id, tenant_id, source, source_type,
                    chunk_count, file_size, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (tenant_id, source) DO UPDATE
                SET chunk_count = $5, file_size = $6, updated_at = NOW()
                RETURNING doc_id::text
                """,
                document.document_id,
                self._tenant_id,
                document.path,
                document.source_type,
                document.chunk_count,
                document.size_bytes,
                json.dumps(document.metadata),
            )
            return str(row["doc_id"])
        finally:
            await self._release_conn(conn)

    async def add_chunk(self, chunk: Chunk) -> str:
        """Insert a chunk into the chunks table."""
        conn = await self._get_conn()
        try:
            # Determine chunk_index — use sibling_order or 0
            chunk_index = chunk.sibling_order if chunk.sibling_order is not None else 0
            row = await conn.fetchrow(
                """
                INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index,
                    text, source_type, entity_name, heading_path, file_path,
                    start_line, end_line, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
                RETURNING chunk_id::text
                """,
                chunk.chunk_id,
                self._tenant_id,
                chunk.document_id,
                chunk_index,
                chunk.text,
                chunk.source_type,
                chunk.entity_name,
                json.dumps(chunk.heading_path) if chunk.heading_path else None,
                chunk.metadata.get("file_path") if chunk.metadata else None,
                chunk.start_line,
                chunk.end_line,
                json.dumps(chunk.metadata),
            )
            return str(row["chunk_id"]) if row else chunk.chunk_id
        finally:
            await self._release_conn(conn)

    @asynccontextmanager
    async def transaction(self):
        """Acquire a connection, set tenant context, wrap in a Postgres transaction."""
        conn = await self._pool.acquire()
        self._conn = conn
        try:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            async with conn.transaction():
                yield
        finally:
            self._conn = None
            await self._pool.release(conn)

    async def close(self) -> None:
        """No-op — pool is managed by server_wiring."""
        pass