"""First-class hop traversal: shared SQL, store parity, determinism.

The storage-portability program's §5.2 contract, hermetic end to end:

  * The DuckDB store runs the REAL ``graph_sql`` builders on a real
    in-memory DuckDB — the diamond scenario pins rows, min-depth merge,
    the (depth, name, entity_id) total order, and limit behavior.
  * ``PostgresGraphStore`` is called through an asyncpg-conn fake that
    captures the SQL it would run; the captured text must byte-match the
    DuckDB store's text (same builder ⇒ same shape on both engines), and
    its canned rows pin the shared ``traversal_row`` shaping.
  * Error translation maps DuckDB failures onto the shared taxonomy.

No Postgres, no AGE, no Ollama.
"""

from __future__ import annotations

from typing import Any, cast

import asyncpg
import duckdb
import pytest

from corpus_kb.errors import MigrationMissingError
from corpus_kb.storage.duckdb_store import DuckDbGraphStore, _translate_error
from corpus_kb.storage.graph_store import PostgresGraphStore
from corpus_kb.utils.models import Entity, Relation

TENANT = "00000000-0000-0000-0000-000000000001"

_DDL = (
    """
    CREATE TABLE entities (
        entity_id VARCHAR PRIMARY KEY,
        tenant_id VARCHAR NOT NULL,
        name VARCHAR NOT NULL,
        entity_type VARCHAR NOT NULL,
        source_document_id VARCHAR,
        metadata VARCHAR,
        UNIQUE (tenant_id, name, entity_type)
    )
    """,
    """
    CREATE TABLE relations (
        relation_id VARCHAR PRIMARY KEY,
        tenant_id VARCHAR NOT NULL,
        source_entity_id VARCHAR NOT NULL,
        target_entity_id VARCHAR NOT NULL,
        relation_type VARCHAR NOT NULL,
        weight DOUBLE,
        metadata VARCHAR,
        chunk_id VARCHAR,
        UNIQUE (tenant_id, source_entity_id, target_entity_id, relation_type, chunk_id)
    )
    """,
)

# Diamond: a->b->d, a->c->d, plus the back edge d->a (cycle guard food).
_SEED_ENTITIES = [
    ("a", "A-Node", "concept"),
    ("b", "B-Node", "concept"),
    ("c", "C-Node", "idea"),
    ("d", "D-Node", "concept"),
]
_SEED_RELATIONS = [
    ("r1", "a", "b", "kinds"),
    ("r2", "a", "c", "other"),
    ("r3", "b", "d", "kinds"),
    ("r4", "c", "d", "other"),
    ("r5", "d", "a", "kinds"),
]


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    for ddl in _DDL:
        conn.execute(ddl)
    for entity_id, name, entity_type in _SEED_ENTITIES:
        conn.execute(
            "INSERT INTO entities VALUES (?, ?, ?, ?, NULL, '{}')",
            [entity_id, TENANT, name, entity_type],
        )
    for relation_id, source, target, relation_type in _SEED_RELATIONS:
        conn.execute(
            "INSERT INTO relations VALUES (?, ?, ?, ?, ?, 1.0, '{}', NULL)",
            [relation_id, TENANT, source, target, relation_type],
        )


@pytest.fixture
def store() -> DuckDbGraphStore:
    """One DuckDbGraphStore over a seeded in-memory DuckDB."""
    conn = duckdb.connect(":memory:")
    _seed(conn)
    return DuckDbGraphStore(conn, tenant_id=TENANT)


# ============================================================================
# DuckDB store: the real traversal legs
# ============================================================================


async def test_duckdb_neighbors_out_with_relation_filter(store: DuckDbGraphStore) -> None:
    """out-neighbors 2 hops over 'kinds' edges: b@1, d@2 in total order."""
    rows = await store.neighbors("a", hops=2, direction="out", relation_types=["kinds"])
    assert [(row["entity_id"], row["depth"]) for row in rows] == [("b", 1), ("d", 2)]
    assert rows[0] == {
        "entity_id": "b",
        "name": "B-Node",
        "entity_type": "concept",
        "metadata": {},
        "depth": 1,
    }


async def test_duckdb_neighbors_in_and_both(store: DuckDbGraphStore) -> None:
    """in-neighbors of a is d (back edge); both adds b and c at depth 1."""
    inward = await store.neighbors("a", hops=1, direction="in")
    assert [row["entity_id"] for row in inward] == ["d"]
    both = await store.neighbors("a", hops=1, direction="both")
    assert [row["entity_id"] for row in both] == ["b", "c", "d"]


async def test_duckdb_traverse_merges_diamond_to_min_depth(store: DuckDbGraphStore) -> None:
    """The recursive walk keeps each node once, at its shallowest depth."""
    rows = await store.traverse("a", max_depth=5)
    assert [(row["entity_id"], row["depth"]) for row in rows] == [
        ("b", 1),
        ("c", 1),
        ("d", 1),
    ]
    assert "a" not in {row["entity_id"] for row in rows}


async def test_duckdb_traverse_limit_and_entity_filter(store: DuckDbGraphStore) -> None:
    """LIMIT truncates in order; entity_types filters the returned nodes."""
    rows = await store.traverse("a", max_depth=5, limit=2)
    assert [row["entity_id"] for row in rows] == ["b", "c"]
    rows = await store.traverse("a", max_depth=5, entity_types=["concept"])
    assert {row["entity_id"] for row in rows} == {"b", "d"}


async def test_duckdb_traverse_unknown_start_is_empty(store: DuckDbGraphStore) -> None:
    rows = await store.traverse("ghost", max_depth=3)
    assert rows == []


async def test_duckdb_search_and_relations_carry_total_orders(
    store: DuckDbGraphStore,
) -> None:
    """(name, entity_id) for search; relation_id ASC for relations."""
    store._conn.execute(
        "INSERT INTO entities VALUES ('a2', ?, 'A-Node', 'idea', NULL, '{}')", [TENANT]
    )
    entities = await store.search_entities("A-Node")
    assert [entity.entity_id for entity in entities] == ["a", "a2"]
    relations = await store.get_entity_relations("a")
    assert [relation.relation_id for relation in relations] == ["r1", "r2", "r5"]


async def test_duckdb_add_and_upsert_roundtrip(store: DuckDbGraphStore) -> None:
    """add_entity/add_relation dedupe on the natural key, like PG.

    The replay uses a FRESH surrogate id (the real caller shape: extraction
    re-runs), so the arbiter is the natural-key constraint, not the PK.
    """
    first = Entity(entity_id="z", name="Z-Node", entity_type="concept", source_type="code")
    assert await store.add_entity(first) == "z"
    replay = Entity(entity_id="z2", name="Z-Node", entity_type="concept", source_type="code")
    assert await store.add_entity(replay) == "z"
    # Relations dedupe on the migration-008 arbiter; chunk_id participates,
    # and a NULL chunk_id dedupes on neither engine (NULLs stay distinct).
    relation = Relation(
        relation_id="r9",
        source_entity_id="z",
        target_entity_id="a",
        relation_type="kinds",
        chunk_id="c1",
    )
    assert await store.add_relation(relation) == "r9"
    duplicate = Relation(
        relation_id="r10",
        source_entity_id="z",
        target_entity_id="a",
        relation_type="kinds",
        chunk_id="c1",
    )
    assert await store.add_relation(duplicate) == "r9"
    assert len(await store.get_entity_relations("z")) == 1


async def test_duckdb_bfs_shapes_visited(store: DuckDbGraphStore) -> None:
    result = await store.bfs("a", max_depth=2)
    assert result["visited"]["a"] == 0
    assert result["visited"]["d"] == 1


async def test_duckdb_transaction_commits_and_rolls_back(store: DuckDbGraphStore) -> None:
    """Outermost scope commits; a failure rolls the whole scope back."""
    entity = Entity(entity_id="y", name="Y-Node", entity_type="concept", source_type="code")
    async with store.transaction():
        await store.add_entity(entity)
    assert await store.get_entity("y") is not None

    entity2 = Entity(entity_id="w", name="W-Node", entity_type="concept", source_type="code")
    with pytest.raises(RuntimeError):
        async with store.transaction():
            await store.add_entity(entity2)
            raise RuntimeError("caller failed")
    assert await store.get_entity("w") is None


async def test_duckdb_nested_transaction_is_a_depth_counter(
    store: DuckDbGraphStore,
) -> None:
    """An inner scope does not commit or roll back the outer one (§3.1)."""
    entity = Entity(entity_id="v", name="V-Node", entity_type="concept", source_type="code")
    async with store.transaction(), store.transaction():
        await store.add_entity(entity)
    assert await store.get_entity("v") is not None


def test_missing_table_translates_to_migration_missing() -> None:
    """A CatalogException maps onto the taxonomy's migration-missing row."""
    import duckdb as duckdb_module

    conn = duckdb_module.connect(":memory:")
    try:
        conn.execute("SELECT * FROM entities")
    except duckdb_module.CatalogException as exc:
        translated = _translate_error(exc)
        assert isinstance(translated, MigrationMissingError)
        assert "corpus-kb setup" in translated.remediation
    finally:
        conn.close()


def test_file_lock_translates_to_database_unavailable() -> None:
    """The program string bank's single-writer message names the fix."""
    import duckdb as duckdb_module

    translated = _translate_error(duckdb_module.IOException("file is locked"))
    assert "Another program is using this database file." in str(translated)


# ============================================================================
# Postgres store: same builder, same shaping (parity leg)
# ============================================================================


class _RecordingConn:
    """asyncpg conn fake: capture SQL, replay canned traversal rows."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.fetch_sql: str | None = None
        self.fetch_args: tuple[object, ...] = ()

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.fetch_sql = sql
        self.fetch_args = args
        return self.rows


class _SQLRecorder:
    """DuckDB conn proxy that records the last executed SQL text."""

    def __init__(self, inner: duckdb.DuckDBPyConnection) -> None:
        self._inner = inner
        self.last_sql: str | None = None

    def execute(self, sql: str, args: Any = None) -> Any:
        self.last_sql = sql
        return self._inner.execute(sql, args)


async def test_pg_and_duckdb_stores_share_one_sql_shape() -> None:
    """Byte-identical traversal SQL from both stores for identical args.

    The PG store's ``_conn`` override (its own transaction hook) bypasses
    the pool, so no Postgres is touched: the fake just captures the SQL.
    """
    recorded = _RecordingConn()
    pg = PostgresGraphStore(pool=cast(Any, object()), tenant_id=TENANT)
    pg._conn = cast("asyncpg.Connection", recorded)
    await pg.neighbors("a", hops=2, direction="out", relation_types=["kinds"])

    conn = duckdb.connect(":memory:")
    _seed(conn)
    duck_store = DuckDbGraphStore(conn, tenant_id=TENANT)
    recorder = _SQLRecorder(conn)
    duck_store._conn = cast(Any, recorder)
    await duck_store.neighbors("a", hops=2, direction="out", relation_types=["kinds"])

    assert recorded.fetch_sql == recorder.last_sql
    assert recorded.fetch_args == (TENANT, "a", ["kinds"])
    assert recorded.fetch_sql is not None
    assert "ORDER BY depth ASC, name ASC, entity_id ASC" in recorded.fetch_sql
    assert "corpus." not in recorded.fetch_sql


async def test_pg_store_shapes_rows_through_the_shared_helper() -> None:
    """Canned PG rows map through traversal_row to the pinned row keys."""
    recorded = _RecordingConn(
        [
            {
                "entity_id": "b",
                "name": "B-Node",
                "entity_type": "concept",
                "metadata": '{"k": 1}',
                "depth": 2,
            }
        ]
    )
    pg = PostgresGraphStore(pool=cast(Any, object()), tenant_id=TENANT)
    pg._conn = cast("asyncpg.Connection", recorded)
    rows = await pg.traverse("a", max_depth=2)
    assert rows == [
        {
            "entity_id": "b",
            "name": "B-Node",
            "entity_type": "concept",
            "metadata": {"k": 1},
            "depth": 2,
        }
    ]
    assert recorded.fetch_sql is not None
    assert "WITH RECURSIVE walk" in recorded.fetch_sql
