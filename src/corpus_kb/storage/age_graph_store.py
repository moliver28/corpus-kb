# allow: SIZE_OK — one responsibility (AGE-backed GraphStore); most lines are
# declarative cypher/SQL text plus 10 thin ABC-mandated methods, and the task
# contract for todo 2a forbids splitting constants into a second module.
"""Apache AGE graph store — Level 2 GraphStore backend using openCypher.

Entities are vertices (:Entity) and relations are edges ([:REL]) inside the
AGE graph ``corpus_kb`` (created by migrations/004_enable_age.sql). All graph
operations run through AGE's cypher() SQL function with an agtype parameter
map — untrusted values are never interpolated into cypher text. Provenance
records (documents/chunks) stay in relational tables, mirroring
PostgresGraphStore. When the AGE extension or graph is missing, every
operation raises AgeUnavailableError naming the cause.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncpg

from ..utils.models import Chunk, Document, Entity, Relation
from .graph_store import DEFAULT_TENANT_ID, GraphStore

logger = logging.getLogger(__name__)

GRAPH_NAME = "corpus_kb"
MAX_BFS_DEPTH = 25

_ENTITY_COLUMNS = (
    "entity_id agtype, name agtype, entity_type agtype, metadata agtype, source_document_id agtype"
)

_ADD_ENTITY = """
MERGE (e:Entity {tenant_id: $tenant_id, name: $name, entity_type: $entity_type})
ON CREATE SET e.entity_id = $entity_id, e.metadata = $metadata,
              e.source_document_id = $source_document_id
RETURN e.entity_id AS entity_id
"""

_ADD_RELATION = """
MATCH (s:Entity), (t:Entity)
WHERE s.entity_id = $source_entity_id AND s.tenant_id = $tenant_id
  AND t.entity_id = $target_entity_id AND t.tenant_id = $tenant_id
MERGE (s)-[r:REL {tenant_id: $tenant_id, relation_type: $relation_type}]->(t)
ON CREATE SET r.relation_id = $relation_id, r.weight = $weight,
              r.source_entity_id = $source_entity_id,
              r.target_entity_id = $target_entity_id, r.metadata = $metadata
RETURN r.relation_id AS relation_id
"""

_GET_ENTITY = """
MATCH (e:Entity)
WHERE e.entity_id = $entity_id AND e.tenant_id = $tenant_id
RETURN e.entity_id AS entity_id, e.name AS name, e.entity_type AS entity_type,
       e.metadata AS metadata, e.source_document_id AS source_document_id
"""

_SEARCH = """
MATCH (e:Entity)
WHERE e.tenant_id = $tenant_id AND toLower(e.name) CONTAINS toLower($name)
RETURN e.entity_id AS entity_id, e.name AS name, e.entity_type AS entity_type,
       e.metadata AS metadata, e.source_document_id AS source_document_id
ORDER BY e.name
"""

_SEARCH_TYPED = """
MATCH (e:Entity)
WHERE e.tenant_id = $tenant_id AND toLower(e.name) CONTAINS toLower($name)
  AND e.entity_type = $entity_type
RETURN e.entity_id AS entity_id, e.name AS name, e.entity_type AS entity_type,
       e.metadata AS metadata, e.source_document_id AS source_document_id
ORDER BY e.name
"""

_GET_RELATIONS = """
MATCH (e:Entity)-[r:REL]-()
WHERE e.entity_id = $entity_id AND e.tenant_id = $tenant_id
  AND r.tenant_id = $tenant_id
RETURN DISTINCT r.relation_id AS relation_id,
       r.source_entity_id AS source_entity_id,
       r.target_entity_id AS target_entity_id,
       r.relation_type AS relation_type, r.weight AS weight, r.metadata AS metadata
"""

_BFS_START = """
MATCH (a:Entity)
WHERE a.entity_id = $start_id AND a.tenant_id = $tenant_id
RETURN a.entity_id AS eid
"""

_BFS_TRAVERSE = """
MATCH p = (a:Entity)-[:REL*1..{depth}]-(b:Entity)
WHERE a.entity_id = $start_id AND a.tenant_id = $tenant_id
  AND b.tenant_id = $tenant_id
RETURN b.entity_id AS eid, min(length(p)) AS depth
"""

_AGE_ABSENCE_MARKERS = ("ag_catalog", "agtype", "cypher", "$libdir/age")


class AgeUnavailableError(RuntimeError):
    """Raised when the Apache AGE backend cannot serve graph operations."""


def _age_unavailable(detail: str) -> AgeUnavailableError:
    return AgeUnavailableError(
        f"Apache AGE graph backend unavailable: {detail}. "
        f"The 'age' extension and graph '{GRAPH_NAME}' are required "
        "(see migrations/004_enable_age.sql); install Apache AGE or use "
        "PostgresGraphStore (graph.backend=postgres)."
    )


def _is_age_absence_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _AGE_ABSENCE_MARKERS)


def _ag(value: object) -> object:
    """Decode an agtype text value (JSON-shaped) into a Python object."""
    if value is None or isinstance(value, (bool, int, float, dict, list)):
        return value
    text = str(value)
    try:
        return json.loads(text)
    except ValueError:
        return text


def _ag_str(value: object) -> str | None:
    parsed = _ag(value)
    return None if parsed is None else str(parsed)


def _ag_dict(value: object) -> dict[str, object]:
    parsed = _ag(value)
    if isinstance(parsed, dict):
        return {str(k): v for k, v in parsed.items()}
    return {}


def _entity_from_row(row: asyncpg.Record) -> Entity:
    return Entity(
        entity_id=str(_ag(row["entity_id"])),
        name=str(_ag(row["name"])),
        entity_type=str(_ag(row["entity_type"])),
        source_type="code",
        source_document_id=_ag_str(row["source_document_id"]),
        metadata=_ag_dict(row["metadata"]),
    )


def _relation_from_row(row: asyncpg.Record) -> Relation:
    weight = _ag(row["weight"])
    return Relation(
        relation_id=str(_ag(row["relation_id"])),
        source_entity_id=str(_ag(row["source_entity_id"])),
        target_entity_id=str(_ag(row["target_entity_id"])),
        relation_type=str(_ag(row["relation_type"])),
        weight=float(weight) if isinstance(weight, (int, float)) else 1.0,
        metadata=_ag_dict(row["metadata"]),
    )


@dataclass(frozen=True, slots=True)
class _CypherSpec:
    """A cypher statement body plus its cypher() result column definition."""

    query: str
    columns: str


class AgeGraphStore(GraphStore):
    """GraphStore backend executing openCypher via Apache AGE's cypher()."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self._pool = pool
        self._tenant_id = tenant_id
        self._conn: asyncpg.Connection | None = None
        self._age_checked = False

    async def _get_conn(self) -> asyncpg.Connection:
        """Return the transaction connection, or acquire + set up a pooled one."""
        if self._conn is not None:
            return self._conn
        conn = await self._pool.acquire()
        try:
            await self._setup_conn(conn)
        except BaseException:
            await self._pool.release(conn)
            raise
        return conn

    async def _release_conn(self, conn: asyncpg.Connection) -> None:
        """Release a pooled connection when not inside a transaction."""
        if self._conn is None:
            await self._pool.release(conn)

    async def _setup_conn(self, conn: asyncpg.Connection) -> None:
        """Probe AGE availability once, then LOAD age + search_path + tenant."""
        if not self._age_checked:
            await self._probe_age(conn)
            self._age_checked = True
        try:
            await conn.execute("LOAD 'age'")
            await conn.execute('SET search_path = ag_catalog, "", public')
        except asyncpg.PostgresError as exc:
            if _is_age_absence_error(exc):
                raise _age_unavailable(f"session setup failed: {exc}") from exc
            raise
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)",
            self._tenant_id,
        )

    async def _probe_age(self, conn: asyncpg.Connection) -> None:
        """Fail fast with a clear error when the extension or graph is absent."""
        ext = await conn.fetchrow(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'age') AS ok"
        )
        if not ext or not ext["ok"]:
            raise _age_unavailable("extension 'age' is not installed on this PostgreSQL server")
        try:
            graph = await conn.fetchrow(
                "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1) AS ok",
                GRAPH_NAME,
            )
        except asyncpg.PostgresError as exc:
            raise _age_unavailable(f"cannot inspect ag_catalog.ag_graph: {exc}") from exc
        if not graph or not graph["ok"]:
            raise _age_unavailable(f"graph '{GRAPH_NAME}' does not exist in ag_catalog.ag_graph")

    async def _run_cypher(
        self,
        conn: asyncpg.Connection,
        spec: _CypherSpec,
        params: Mapping[str, object],
    ) -> list[asyncpg.Record]:
        """Execute a cypher() call; all values travel in one agtype param map."""
        sql = (
            f"SELECT * FROM cypher('{GRAPH_NAME}', $${spec.query}$$, $1::agtype) "
            f"AS ({spec.columns})"
        )
        try:
            return await conn.fetch(sql, json.dumps(dict(params)))
        except asyncpg.PostgresError as exc:
            if _is_age_absence_error(exc):
                raise _age_unavailable(f"cypher query failed: {exc}") from exc
            raise

    async def add_entity(self, entity: Entity) -> str:
        """MERGE an :Entity vertex keyed on (tenant_id, name, entity_type)."""
        conn = await self._get_conn()
        try:
            rows = await self._run_cypher(
                conn,
                _CypherSpec(_ADD_ENTITY, "entity_id agtype"),
                {
                    "tenant_id": self._tenant_id,
                    "name": entity.name,
                    "entity_type": entity.entity_type,
                    "entity_id": entity.entity_id,
                    "source_document_id": entity.source_document_id,
                    "metadata": entity.metadata,
                },
            )
            return str(_ag(rows[0]["entity_id"])) if rows else entity.entity_id
        finally:
            await self._release_conn(conn)

    async def add_relation(self, relation: Relation) -> str:
        """MERGE a :REL edge between two existing entity vertices."""
        conn = await self._get_conn()
        try:
            rows = await self._run_cypher(
                conn,
                _CypherSpec(_ADD_RELATION, "relation_id agtype"),
                {
                    "tenant_id": self._tenant_id,
                    "source_entity_id": relation.source_entity_id,
                    "target_entity_id": relation.target_entity_id,
                    "relation_type": relation.relation_type,
                    "relation_id": relation.relation_id,
                    "weight": relation.weight if relation.weight else 1.0,
                    "metadata": relation.metadata,
                },
            )
            if not rows:
                raise ValueError(
                    "add_relation failed: source or target entity not found "
                    f"(source={relation.source_entity_id}, "
                    f"target={relation.target_entity_id})"
                )
            return str(_ag(rows[0]["relation_id"]))
        finally:
            await self._release_conn(conn)

    async def get_entity(self, entity_id: str) -> Entity | None:
        """Fetch an entity vertex by ID."""
        conn = await self._get_conn()
        try:
            rows = await self._run_cypher(
                conn,
                _CypherSpec(_GET_ENTITY, _ENTITY_COLUMNS),
                {"entity_id": entity_id, "tenant_id": self._tenant_id},
            )
            return _entity_from_row(rows[0]) if rows else None
        finally:
            await self._release_conn(conn)

    async def search_entities(self, name: str, entity_type: str | None = None) -> list[Entity]:
        """Search entity vertices by case-insensitive name contains + type."""
        conn = await self._get_conn()
        try:
            params: dict[str, object] = {
                "name": name,
                "tenant_id": self._tenant_id,
            }
            query = _SEARCH
            if entity_type is not None:
                query = _SEARCH_TYPED
                params["entity_type"] = entity_type
            rows = await self._run_cypher(conn, _CypherSpec(query, _ENTITY_COLUMNS), params)
            return [_entity_from_row(row) for row in rows]
        finally:
            await self._release_conn(conn)

    async def get_entity_relations(self, entity_id: str) -> list[Relation]:
        """Get all edges where the entity is source or target (undirected)."""
        conn = await self._get_conn()
        try:
            rows = await self._run_cypher(
                conn,
                _CypherSpec(
                    _GET_RELATIONS,
                    "relation_id agtype, source_entity_id agtype, "
                    "target_entity_id agtype, relation_type agtype, "
                    "weight agtype, metadata agtype",
                ),
                {"entity_id": entity_id, "tenant_id": self._tenant_id},
            )
            return [_relation_from_row(row) for row in rows]
        finally:
            await self._release_conn(conn)

    async def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict[str, object]:
        """BFS via a variable-length cypher MATCH; depth is int-validated."""
        try:
            depth = int(max_depth)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"max_depth must be an integer, got {max_depth!r}") from exc
        if not 1 <= depth <= MAX_BFS_DEPTH:
            raise ValueError(f"max_depth must be between 1 and {MAX_BFS_DEPTH}, got {depth}")
        conn = await self._get_conn()
        try:
            params = {"start_id": start_entity_id, "tenant_id": self._tenant_id}
            start_rows = await self._run_cypher(conn, _CypherSpec(_BFS_START, "eid agtype"), params)
            visited: dict[str, int] = {}
            if not start_rows:
                return {
                    "start_entity_id": start_entity_id,
                    "max_depth": depth,
                    "visited": visited,
                }
            visited[start_entity_id] = 0
            rows = await self._run_cypher(
                conn,
                _CypherSpec(_BFS_TRAVERSE.format(depth=depth), "eid agtype, depth agtype"),
                params,
            )
            for row in rows:
                eid = str(_ag(row["eid"]))
                row_depth = int(str(_ag(row["depth"])))
                if eid not in visited or row_depth < visited[eid]:
                    visited[eid] = row_depth
            return {
                "start_entity_id": start_entity_id,
                "max_depth": depth,
                "visited": visited,
            }
        finally:
            await self._release_conn(conn)

    async def add_document(self, document: Document) -> str:
        """Insert a document into the relational documents table."""
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
            if row:
                return str(row["doc_id"])
            return document.document_id
        finally:
            await self._release_conn(conn)

    async def add_chunk(self, chunk: Chunk) -> str:
        """Insert a chunk into the relational chunks table."""
        conn = await self._get_conn()
        try:
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
        """Acquire a connection, run AGE + tenant setup, wrap writes in a transaction."""
        conn = await self._pool.acquire()
        try:
            await self._setup_conn(conn)
        except BaseException:
            await self._pool.release(conn)
            raise
        self._conn = conn
        try:
            async with conn.transaction():
                yield
        finally:
            self._conn = None
            await self._pool.release(conn)

    async def close(self) -> None:
        """No-op — pool is managed by server_wiring."""
        pass
