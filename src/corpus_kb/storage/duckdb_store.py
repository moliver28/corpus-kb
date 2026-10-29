# allow: SIZE_OK — one responsibility (DuckDB-backed GraphStore); most lines
# are declarative SQL text plus the ABC-mandated thin methods.
"""DuckDB graph store — the storage-portability program's Level 3 backend.

Same ``GraphStore`` ABC, same traversal SQL as ``PostgresGraphStore`` (both
render from ``storage/graph_sql.py``), over one DuckDB connection. The
store stays OFF the SqlGateway: it owns its transaction semantics on the
connection (§3.1). Table names stay unqualified (the gateway guard rejects
``corpus.`` on DuckDB; graph SQL is naturally unqualified). Every predicate-
able statement carries an explicit ``tenant_id`` predicate.

Concurrency posture (§1.1): DuckDB is single-writer; one asyncio lock
serializes transaction scopes and one threading lock guards individual
sync calls under ``asyncio.to_thread``. Errors translate at this boundary
into the shared taxonomy — the ``_age_unavailable`` precedent, with the
program's string bank for the file-lock case.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from ..errors import DatabaseUnavailableError, MigrationMissingError
from ..utils.models import Entity, Relation
from .duckpgq import ensure_duckpgq, merge_min_depth_rows, traversal_statements
from .graph_sql import neighbors_sql, traversal_row, traverse_sql, validate_depth, validate_hops
from .graph_store import DEFAULT_TENANT_ID, GraphStore

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

logger = logging.getLogger(__name__)


def _translate_error(exc: Exception) -> Exception:
    """Map DuckDB failures onto the shared taxonomy (no new error classes)."""
    import duckdb

    text = str(exc).lower()
    if isinstance(exc, duckdb.IOException) or "lock" in text or "conflict" in text:
        return DatabaseUnavailableError(
            "Another program is using this database file. Close it, then try again.",
            remediation="Close the other program, then run the command again.",
        )
    if isinstance(exc, duckdb.CatalogException):
        return MigrationMissingError(
            f"Required table or graph object is missing: {exc}",
            remediation="Run corpus-kb setup. Then try again.",
        )
    return exc


class DuckDbGraphStore(GraphStore):
    """GraphStore backend over one DuckDB connection (sync, thread-offloaded)."""

    def __init__(
        self,
        conn: DuckDBPyConnection,
        tenant_id: str = DEFAULT_TENANT_ID,
        traversal_engine: str = "cte",
    ) -> None:
        self._conn = conn
        self._tenant_id = tenant_id
        self._stmt_lock = threading.Lock()
        self._tx_lock = asyncio.Lock()
        self._tx_depth = 0
        # DuckPGQ opt-in (graph.duckdb_graph_backend). None = not probed
        # yet; False after any failure = permanent CTE for this connection.
        self._traversal_engine = traversal_engine
        self._pgq_active: bool | None = None if traversal_engine == "duckpgq" else False
        self._pgq_warned = False

    # -- duckpgq branch ------------------------------------------------------

    def _ensure_duckpgq(self) -> None:
        """Install/load DuckPGQ once, under the statement lock."""
        with self._stmt_lock:
            ensure_duckpgq(self._conn)

    async def _duckpgq_rows(self, build: Callable[[], list[str]]) -> list[dict[str, Any]] | None:
        """Rows through DuckPGQ, or None to mean "run the CTE path".

        The one-shot probe installs and loads the extension and creates
        the static property graph. ANY failure warns once (typed
        DuckPGQUnavailableError text) and turns the branch off for this
        connection, permanently; the current call then takes the CTE path
        so the caller still gets CTE-identical rows.
        """
        if self._pgq_active is False:
            return None
        try:
            if self._pgq_active is None:
                if self._tx_depth:
                    await asyncio.to_thread(self._ensure_duckpgq)
                else:
                    async with self._tx_lock:
                        await asyncio.to_thread(self._ensure_duckpgq)
            # One statement per GRAPH_TABLE arm: a UNION ALL of two arms
            # is a fatal crasher on duckpgq builds (see duckpgq.py).
            rows: list[dict[str, Any]] = []
            for sql in build():
                rows.extend(await self._fetch(sql, []))
            self._pgq_active = True
            return rows
        except Exception as exc:
            self._pgq_active = False
            if not self._pgq_warned:
                self._pgq_warned = True
                logger.warning("DuckPGQ traversal unavailable; using the CTE path: %s", exc)
            return None

    # -- execution boundary --------------------------------------------------

    async def _fetch(self, sql: str, args: list[object]) -> list[dict[str, Any]]:
        """Run one statement off-loop; return rows as key-addressable dicts."""
        conn, stmt_lock = self._conn, self._stmt_lock

        def _query() -> list[dict[str, Any]]:
            with stmt_lock:
                cursor = conn.execute(sql, args)
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

        import duckdb

        try:
            if self._tx_depth:
                return await asyncio.to_thread(_query)
            async with self._tx_lock:
                return await asyncio.to_thread(_query)
        except duckdb.Error as exc:
            raise _translate_error(exc) from exc

    async def _execute(self, sql: str, args: list[object]) -> Any:
        """Run one write statement off-loop; return its first result row."""
        import duckdb

        try:
            if self._tx_depth:
                return await asyncio.to_thread(self._run_write, sql, args)
            async with self._tx_lock:
                return await asyncio.to_thread(self._run_write, sql, args)
        except duckdb.Error as exc:
            raise _translate_error(exc) from exc

    def _run_write(self, sql: str, args: list[object]) -> Any:
        with self._stmt_lock:
            cursor = self._conn.execute(sql, args)
            if cursor.description is not None:
                columns = [column[0] for column in cursor.description]
                rows = cursor.fetchall()
                return dict(zip(columns, rows[0], strict=False)) if rows else None
            return None

    # -- writes --------------------------------------------------------------

    async def add_entity(self, entity: Entity) -> str:
        """Insert an entity; same upsert contract as the Postgres store."""
        row = await self._execute(
            """
            INSERT INTO entities (entity_id, tenant_id, name, entity_type,
                source_document_id, metadata)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
            RETURNING entity_id::text AS entity_id
            """,
            [
                entity.entity_id,
                self._tenant_id,
                entity.name,
                entity.entity_type,
                entity.source_document_id,
                json.dumps(entity.metadata),
            ],
        )
        if row:
            return str(row["entity_id"])
        row = await self._execute(
            """
            SELECT entity_id::text AS entity_id FROM entities
            WHERE tenant_id = $1 AND name = $2 AND entity_type = $3
            """,
            [self._tenant_id, entity.name, entity.entity_type],
        )
        return str(row["entity_id"]) if row else entity.entity_id

    async def add_relation(self, relation: Relation) -> str:
        """Insert a relation; same upsert contract as the Postgres store."""
        row = await self._execute(
            """
            INSERT INTO relations (relation_id, tenant_id, source_entity_id,
                target_entity_id, relation_type, weight, metadata, chunk_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (tenant_id, source_entity_id, target_entity_id,
                relation_type, chunk_id) DO NOTHING
            RETURNING relation_id::text AS relation_id
            """,
            [
                relation.relation_id,
                self._tenant_id,
                relation.source_entity_id,
                relation.target_entity_id,
                relation.relation_type,
                relation.weight if relation.weight else 1.0,
                json.dumps(relation.metadata),
                relation.chunk_id,
            ],
        )
        if row:
            return str(row["relation_id"])
        row = await self._execute(
            """
            SELECT relation_id::text AS relation_id FROM relations
            WHERE tenant_id = $1 AND source_entity_id = $2
              AND target_entity_id = $3 AND relation_type = $4
              AND chunk_id IS NOT DISTINCT FROM $5
            """,
            [
                self._tenant_id,
                relation.source_entity_id,
                relation.target_entity_id,
                relation.relation_type,
                relation.chunk_id,
            ],
        )
        return str(row["relation_id"]) if row else relation.relation_id

    # -- reads ---------------------------------------------------------------

    async def get_entity(self, entity_id: str) -> Entity | None:
        """Fetch an entity by ID."""
        rows = await self._fetch(
            """
            SELECT entity_id::text AS entity_id, name, entity_type,
                   metadata::text AS metadata, source_document_id::text
                   AS source_document_id
            FROM entities WHERE entity_id = $1 AND tenant_id = $2
            """,
            [entity_id, self._tenant_id],
        )
        if not rows:
            return None
        row = rows[0]
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        return Entity(
            entity_id=str(row["entity_id"]),
            name=str(row["name"]),
            entity_type=str(row["entity_type"]),
            source_type="code",
            source_document_id=row["source_document_id"],
            metadata=metadata,
        )

    async def search_entities(self, name: str, entity_type: str | None = None) -> list[Entity]:
        """Search entities by name contains + optional type (stable order)."""
        pattern = f"%{name}%"
        sql = """
            SELECT entity_id::text AS entity_id, name, entity_type,
                   metadata::text AS metadata, source_document_id::text
                   AS source_document_id
            FROM entities
            WHERE tenant_id = $1 AND name ILIKE $2 {type_filter}
            ORDER BY name ASC, entity_id ASC
        """
        args: list[object] = [self._tenant_id, pattern]
        type_filter = ""
        if entity_type:
            type_filter = "AND entity_type = $3"
            args.append(entity_type)
        rows = await self._fetch(sql.format(type_filter=type_filter), args)
        entities: list[Entity] = []
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            entities.append(
                Entity(
                    entity_id=str(row["entity_id"]),
                    name=str(row["name"]),
                    entity_type=str(row["entity_type"]),
                    source_type="code",
                    source_document_id=row["source_document_id"],
                    metadata=metadata,
                )
            )
        return entities

    async def get_entity_relations(self, entity_id: str) -> list[Relation]:
        """Get all relations where the entity is source or target."""
        rows = await self._fetch(
            """
            SELECT relation_id::text AS relation_id,
                   source_entity_id::text AS source_entity_id,
                   target_entity_id::text AS target_entity_id,
                   relation_type, weight, metadata::text AS metadata
            FROM relations
            WHERE tenant_id = $1
              AND (source_entity_id = $2 OR target_entity_id = $2)
            ORDER BY relation_id ASC
            """,
            [self._tenant_id, entity_id],
        )
        relations: list[Relation] = []
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            relations.append(
                Relation(
                    relation_id=str(row["relation_id"]),
                    source_entity_id=str(row["source_entity_id"]),
                    target_entity_id=str(row["target_entity_id"]),
                    relation_type=str(row["relation_type"]),
                    weight=float(row["weight"]) if row["weight"] is not None else 1.0,
                    metadata=metadata,
                )
            )
        return relations

    async def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict[str, Any]:
        """Breadth-first search; DuckPGQ when active, else the recursive CTE."""
        rows = await self._duckpgq_rows(
            lambda: traversal_statements(
                "both", max_depth, self._tenant_id, start_entity_id, include_start=True
            )
        )
        if rows is not None:
            visited = {
                str(row["entity_id"]): int(row["depth"]) for row in merge_min_depth_rows(rows)
            }
            return {
                "start_entity_id": start_entity_id,
                "max_depth": max_depth,
                "visited": visited,
            }
        rows = await self._fetch(
            """
            WITH RECURSIVE graph_bfs AS (
                SELECT entity_id::text AS eid, 0 AS depth
                FROM entities WHERE entity_id = $1 AND tenant_id = $2
                UNION ALL
                SELECT
                    CASE
                        WHEN r.source_entity_id::text = g.eid
                            THEN r.target_entity_id::text
                        ELSE r.source_entity_id::text
                    END AS eid,
                    g.depth + 1
                FROM graph_bfs g
                JOIN relations r ON r.tenant_id = $2
                    AND (r.source_entity_id::text = g.eid
                         OR r.target_entity_id::text = g.eid)
                WHERE g.depth < $3
            )
            SELECT eid, depth FROM graph_bfs
            """,
            [start_entity_id, self._tenant_id, max_depth],
        )
        visited: dict[str, int] = {}
        for row in rows:
            eid = str(row["eid"])
            depth_value = int(row["depth"])
            if eid not in visited or depth_value < visited[eid]:
                visited[eid] = depth_value
        return {
            "start_entity_id": start_entity_id,
            "max_depth": max_depth,
            "visited": visited,
        }

    async def neighbors(
        self,
        entity_id: str,
        hops: int = 1,
        direction: str = "both",
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fixed self-join chain over relations (shared graph_sql shape)."""
        validate_hops(hops)
        rows = await self._duckpgq_rows(
            lambda: traversal_statements(
                direction, hops, self._tenant_id, entity_id, entity_types, relation_types
            )
        )
        if rows is None:
            sql, args = neighbors_sql(
                hops,
                direction,
                self._tenant_id,
                entity_id,
                relation_types,
                entity_types,
                limit,
            )
            rows = await self._fetch(sql, args)
            return [traversal_row(row) for row in rows]
        return [traversal_row(row) for row in merge_min_depth_rows(rows, limit, exclude=entity_id)]

    async def traverse(
        self,
        entity_id: str,
        max_depth: int = 5,
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """WITH RECURSIVE walk with path-array cycle guard (shared shape)."""
        validate_depth(max_depth)
        rows = await self._duckpgq_rows(
            lambda: traversal_statements(
                "both", max_depth, self._tenant_id, entity_id, entity_types, relation_types
            )
        )
        if rows is None:
            sql, args = traverse_sql(
                max_depth,
                self._tenant_id,
                entity_id,
                relation_types,
                entity_types,
                limit,
            )
            rows = await self._fetch(sql, args)
            return [traversal_row(row) for row in rows]
        return [traversal_row(row) for row in merge_min_depth_rows(rows, limit, exclude=entity_id)]

    # -- lifecycle -----------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Hold the store lock across a DuckDB transaction on the connection.

        Nested scopes are a depth-counter no-op (DuckDB has no SAVEPOINT;
        §3.1 gateway semantics) — they neither begin/commit nor touch the
        already-held lock — so the fail-open degraded-probe contracts port
        unchanged.
        """
        if self._tx_depth:
            self._tx_depth += 1
            try:
                yield
            finally:
                self._tx_depth -= 1
            return
        async with self._tx_lock:
            self._tx_depth += 1
            try:
                self._conn.execute("BEGIN TRANSACTION")
                yield
            except BaseException:
                with self._stmt_lock:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                with self._stmt_lock:
                    self._conn.execute("COMMIT")
            finally:
                self._tx_depth -= 1

    async def close(self) -> None:
        """Close the connection when this store owns it (default: caller's)."""
        return None
