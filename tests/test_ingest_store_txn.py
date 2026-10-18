"""v1 ingest store methods set the tenant GUC inside a transaction.

Regression pin for issue #87 (closes #75): the four v1
:class:`PostgresIngestStore` methods (``store_chunks``/``store_vectors``/
``store_entities``/``store_relations``) historically issued
``set_config('app.current_tenant_id', $1, true)`` on an autocommit
connection OUTSIDE any transaction, where a LOCAL GUC evaporates with its
statement — on FORCE-RLS tables (migration 011+) every following write ran
GUC-less and was rejected. These tests pin the house ledger-method shape:
``conn.transaction()`` is entered and the GUC is its FIRST statement,
before any INSERT/SELECT.

Postgres is not required: a recording connection double (adapted from
``_RecordingConn`` in tests/test_search_honesty.py) marks transaction
edges as BEGIN/COMMIT so statement order is asserted offline.
"""

from __future__ import annotations

from corpus_kb.tools.ingest_store import PostgresIngestStore
from corpus_kb.utils.models import Chunk, Entity, Relation

TENANT = "00000000-0000-0000-0000-000000000003"

GUC_SQL = "SELECT set_config('app.current_tenant_id', $1, true)"


class _RecordingTx:
    """Transaction stand-in: marks entry/exit in the connection's SQL log.

    Exit records ROLLBACK when unwinding through an exception (asyncpg
    semantics), so tests can pin that a failing statement leaves no
    committed partial write.
    """

    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _RecordingConn:
        self._conn.sql.append("BEGIN")
        return self._conn

    async def __aexit__(self, exc_type: object, *exc: object) -> bool:
        self._conn.sql.append("ROLLBACK" if exc_type is not None else "COMMIT")
        return False


class _RecordingConn:
    """Asyncpg-connection double: logs SQL order and transaction edges.

    Nothing here reaches a database: SQL arrives from the store under test
    (already parameterized with ``$1`` placeholders) and is recorded
    verbatim for order assertions — no statement is executed and no SQL
    string is ever built from ``args``.

    Existence probes (no RETURNING) answer None so every store method takes
    its insert path; INSERT..RETURNING echoes the first bound argument back
    as the generated id.
    """

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.bound_args: dict[str, tuple[object, ...]] = {}

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        self.bound_args[sql] = args
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.sql.append(sql)
        self.bound_args[sql] = args
        if "RETURNING" in sql:
            for column in ("chunk_id", "entity_id", "relation_id"):
                if column in sql:
                    return {column: args[0]}
            return {}
        return None

    def transaction(self) -> _RecordingTx:
        return _RecordingTx(self)


class _RecordingPool:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn
        self.acquired = False

    def acquire(self) -> _RecordingPool:
        return self

    async def __aenter__(self) -> _RecordingConn:
        self.acquired = True
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _assert_one_transaction_wraps_all(conn: _RecordingConn) -> None:
    assert conn.sql, "store method issued no statements"
    assert conn.sql[0] == "BEGIN", (
        f"conn.transaction() must be entered before any statement; got {conn.sql[0]!r}"
    )
    assert conn.sql[-1] == "COMMIT", "transaction must still be open when the method returns"
    assert conn.sql.count("BEGIN") == 1 and conn.sql.count("COMMIT") == 1, (
        f"expected exactly one transaction, got: {conn.sql}"
    )


def _assert_guc_first_inside_transaction(conn: _RecordingConn) -> None:
    _assert_one_transaction_wraps_all(conn)
    assert conn.sql[1] == GUC_SQL, (
        f"tenant GUC must be the first statement inside the transaction; got {conn.sql[1]!r}"
    )
    assert conn.bound_args[GUC_SQL] == (TENANT,)
    # Every remaining statement is data DML issued after the GUC, in-txn.
    for sql in conn.sql[2:-1]:
        assert sql.strip().upper().startswith(("INSERT", "UPDATE", "SELECT")), (
            f"unexpected: {sql!r}"
        )


def _chunk() -> Chunk:
    return Chunk(chunk_id="c1", document_id="d1", text="body", source_type="text")


async def test_store_chunks_sets_tenant_guc_inside_transaction() -> None:
    conn = _RecordingConn()
    store = PostgresIngestStore(_RecordingPool(conn), TENANT)

    needs_embedding = await store.store_chunks([_chunk()])

    assert needs_embedding == {"c1"}
    _assert_guc_first_inside_transaction(conn)
    tail = conn.sql[2:-1]
    assert any("FROM chunks" in sql and "chunk_hash" in sql for sql in tail)  # existence probe
    assert any("INSERT INTO chunks" in sql for sql in tail)
    assert any("tombstoned_at = NOW()" in sql for sql in tail)  # trailing tombstone sweep


async def test_store_vectors_sets_tenant_guc_inside_transaction() -> None:
    conn = _RecordingConn()
    store = PostgresIngestStore(_RecordingPool(conn), TENANT)
    chunk = _chunk()
    chunk.embedding = [0.5, 0.25]

    written = await store.store_vectors([chunk], "nomic-embed-text", dimensions=768)

    assert written == 1
    _assert_guc_first_inside_transaction(conn)
    tail = conn.sql[2:-1]
    assert any("INSERT INTO chunks_vectors" in sql for sql in tail)
    assert any("UPDATE chunks SET degraded" in sql for sql in tail)


async def test_store_entities_sets_tenant_guc_inside_transaction() -> None:
    conn = _RecordingConn()
    store = PostgresIngestStore(_RecordingPool(conn), TENANT)
    entity = Entity(name="Alpha", entity_type="Concept", source_type="text")

    id_map = await store.store_entities([entity])

    # The double echoes the RETURNING id back; the map keys extraction id.
    assert id_map == {entity.entity_id: entity.entity_id}
    _assert_guc_first_inside_transaction(conn)
    assert any("INSERT INTO entities" in sql for sql in conn.sql[2:-1])


async def test_store_relations_sets_tenant_guc_inside_transaction() -> None:
    conn = _RecordingConn()
    store = PostgresIngestStore(_RecordingPool(conn), TENANT)
    relations = [Relation(source_entity_id="a", target_entity_id="b", relation_type="DEPENDS_ON")]

    count = await store.store_relations(relations)

    assert count == 1
    _assert_guc_first_inside_transaction(conn)
    assert any("INSERT INTO relations" in sql for sql in conn.sql[2:-1])


async def test_empty_inputs_return_before_touching_the_pool() -> None:
    """The empty-input early return stays ahead of acquire + transaction."""
    conn = _RecordingConn()
    pool = _RecordingPool(conn)
    store = PostgresIngestStore(pool, TENANT)

    assert await store.store_chunks([]) == set()
    assert await store.store_vectors([], "nomic-embed-text") == 0
    assert await store.store_entities([]) == {}
    assert await store.store_relations([]) == 0

    assert conn.sql == []
    assert pool.acquired is False
