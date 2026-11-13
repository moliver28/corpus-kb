"""The SQL route refuses writes (issue #349).

``POST /api/query/sql`` used to run any single statement it received.
The gate lives in ``handlers/query_sql_guard.py`` and runs on both
engines, before any connection opens. The issue's Check cases are pinned
at four levels: the guard unit, the handler arms (fake gateway, fake
pool, real DuckDB file), a live Postgres leg, and the HTTP + socket
surfaces (``test_surface_parity_deep.py``). The typed
``WriteRefusedError`` carries ONE HTTP status (400), ONE socket code
(-32602), and ONE CLI exit code (1).
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from corpus_kb.domain.models import SQLQuery
from corpus_kb.errors import ERROR_MAP, WriteRefusedError
from corpus_kb.handlers.query_crud_reads import sql_query
from corpus_kb.handlers.query_sql_guard import refuse_non_read
from tests.mock_pg import FakeSqlGateway

TENANT = UUID(int=1)

SELECT_OK = "SELECT 1"
DELETE_PLAIN = "DELETE FROM documents"
DELETE_CTE = "WITH x AS (DELETE FROM chunks RETURNING 1) SELECT * FROM x"
TWO_STATEMENTS = "SELECT 1; DROP TABLE tags"


# ---------------------------------------------------------------------------
# The guard unit: reads pass, everything else is refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        SELECT_OK,
        "select 1",
        "  -- a leading comment\nSELECT 1",
        "SELECT 1 -- trailing comment",
        "SELECT 1; -- trailing comment after the semicolon",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SELECT 'DELETE FROM documents' AS note",
        "SELECT 1 /* DROP TABLE tags */",
        'SELECT "delete" FROM t',
        "SELECT $$a;b$$ AS note",
        "SELECT $tag$a;b$tag$ AS note",
        "SELECT * FROM documents WHERE text = 'a;b'",
        "(SELECT 1) UNION (SELECT 2)",
        "TABLE documents",
        "VALUES (1)",
        "SHOW ALL",
        "EXPLAIN SELECT 1",
    ],
)
def test_reads_pass_the_gate(sql: str) -> None:
    refuse_non_read(sql)  # no raise


@pytest.mark.parametrize(
    "sql",
    [
        DELETE_PLAIN,
        DELETE_CTE,
        TWO_STATEMENTS,
        "INSERT INTO tags(name) VALUES ('x')",
        "UPDATE documents SET title = 'x'",
        "TRUNCATE chunks",
        "DROP TABLE tags",
        "CREATE TABLE t(x int)",
        "ALTER TABLE tags ADD COLUMN c int",
        "GRANT SELECT ON documents TO public",
        "SELECT 1 FOR UPDATE",
        "SELECT * INTO newt FROM documents",
        "SELECT nextval('documents_seq')",
        "SELECT setval('documents_seq', 1)",
        "EXPLAIN ANALYZE SELECT 1",
        "PRAGMA table_info('tags')",
        "SET search_path = public",
        "",
        "   ",
        ";;;",
    ],
)
def test_non_reads_are_refused(sql: str) -> None:
    with pytest.raises(WriteRefusedError):
        refuse_non_read(sql)


def test_write_refused_error_is_one_taxonomy_row() -> None:
    assert ERROR_MAP["write_refused"] == (400, -32602, 1)
    assert (
        WriteRefusedError.http_status,
        WriteRefusedError.socket_code,
        WriteRefusedError.cli_exit,
    ) == ERROR_MAP["write_refused"]
    words = WriteRefusedError.remediation.split()
    assert 2 <= len(words) <= 20
    assert WriteRefusedError.remediation[0].isupper()


# ---------------------------------------------------------------------------
# The masking edge cases and the DuckDB net's refusals (issue #349 legs).
# ---------------------------------------------------------------------------


def test_headless_masked_body_is_refused() -> None:
    """A statement with no readable head has nothing to prove."""
    with pytest.raises(WriteRefusedError) as excinfo:
        refuse_non_read("123")
    assert "needs one read statement" in str(excinfo.value)


def test_nested_block_comments_stay_masked() -> None:
    """Postgres-style nested comments hide writes; the read still passes."""
    refuse_non_read("SELECT /* outer /* inner DROP TABLE t */ 1 */ 1")  # no raise


def test_doubled_quote_escape_keeps_one_string() -> None:
    """'' inside a string is an escape; the semicolon inside stays data."""
    refuse_non_read("SELECT 'a''b; DROP TABLE t' AS note")  # no raise


def test_unterminated_string_consumes_the_tail() -> None:
    """An unterminated string never runs half a statement; head rules."""
    refuse_non_read("SELECT 'abc")  # no raise: the head proves the read
    with pytest.raises(WriteRefusedError):
        refuse_non_read("DELETE 'abc")


def test_duckdb_net_refuses_unprovable_sql() -> None:
    """DuckDB's own parser is the second net: junk is refused, not run."""
    import duckdb  # noqa: F401  (the extra is installed for this leg)

    from corpus_kb.handlers.query_sql_guard import refuse_writes_duckdb

    with pytest.raises(WriteRefusedError) as excinfo:
        refuse_writes_duckdb("SELEC nope nope nope")
    assert "could not prove" in str(excinfo.value)


def test_duckdb_net_refuses_two_statements_and_writes() -> None:
    """Two statements and a write both trip DuckDB's own statement list."""
    from corpus_kb.handlers.query_sql_guard import refuse_writes_duckdb

    with pytest.raises(WriteRefusedError) as multi:
        refuse_writes_duckdb("SELECT 1; SELECT 2")
    assert "one statement per call" in str(multi.value)

    with pytest.raises(WriteRefusedError) as writes:
        refuse_writes_duckdb("DELETE FROM documents")
    assert "read statements only" in str(writes.value)


# ---------------------------------------------------------------------------
# The handler arms: refusal happens before any statement runs
# ---------------------------------------------------------------------------


async def test_fake_gateway_runs_the_read_and_never_the_write() -> None:
    gw = FakeSqlGateway({"SELECT 1": [{"ok": 1}]})
    rows = await sql_query(gw, SQLQuery(tenant_id=TENANT, sql=SELECT_OK))
    assert rows == [{"ok": 1}]
    for sql in (DELETE_PLAIN, DELETE_CTE, TWO_STATEMENTS):
        with pytest.raises(WriteRefusedError):
            await sql_query(gw, SQLQuery(tenant_id=TENANT, sql=sql))
    # No refused statement ever reached the gateway.
    assert [sql for sql, _args in gw.calls] == [SELECT_OK]


class _RecordingConn:
    """Fake asyncpg connection that records every statement's text."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def transaction(self) -> _RecordingConn:
        return self

    async def __aenter__(self) -> _RecordingConn:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def execute(self, sql: str, *args: object) -> str:
        self.statements.append(sql)
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, int]]:
        self.statements.append(sql)
        return [{"ok": 1}]


class _RecordingPool:
    def __init__(self) -> None:
        self.conn = _RecordingConn()

    def acquire(self) -> _RecordingPool:
        return self

    async def __aenter__(self) -> _RecordingConn:
        return self.conn

    async def __aexit__(self, *args: object) -> bool:
        return False


async def test_pool_arm_sets_read_only_before_the_query() -> None:
    """The Postgres pool arm: READ ONLY, then the tenant GUC, then the read."""
    pool = _RecordingPool()
    rows = await sql_query(pool, SQLQuery(tenant_id=TENANT, sql=SELECT_OK))
    assert rows == [{"ok": 1}]
    assert pool.conn.statements == [
        "SET TRANSACTION READ ONLY",
        "SELECT set_config('app.current_tenant_id', $1, true)",
        SELECT_OK,
    ]


async def test_duckdb_gateway_refuses_writes_and_keeps_rows(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    from corpus_kb.storage.duckdb_gateway import DuckDbGateway

    gw = await DuckDbGateway.connect(f"duckdb:///{tmp_path / 'ro349.duckdb'}")
    try:
        await gw.execute("CREATE TABLE probe(id int)")
        await gw.execute("INSERT INTO probe VALUES (1)")
        rows = await sql_query(gw, SQLQuery(tenant_id=TENANT, sql="SELECT id FROM probe"))
        assert [dict(row)["id"] for row in rows] == [1]
        for sql in (DELETE_PLAIN, DELETE_CTE, TWO_STATEMENTS):
            with pytest.raises(WriteRefusedError):
                await sql_query(gw, SQLQuery(tenant_id=TENANT, sql=sql))
        count = await sql_query(
            gw, SQLQuery(tenant_id=TENANT, sql="SELECT count(*) AS n FROM probe")
        )
        assert dict(count[0])["n"] == 1
    finally:
        await gw.close()


# ---------------------------------------------------------------------------
# Live Postgres: the whole flow on a scratch database
# ---------------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_live_postgres_read_only_route_changes_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the read passes, the writes refuse, the row count stays."""
    import asyncpg

    from corpus_kb.storage.tenant_conn import tenant_connection
    from tests import research_db

    monkeypatch.setattr(research_db, "DB_NAME", "corpus_kb_ro349_test")
    monkeypatch.setattr(research_db, "DSN", f"{research_db.BASE_DSN}/corpus_kb_ro349_test")
    dsn = await research_db.create_research_db()
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        await pool.execute("CREATE TABLE ro349_probe(id int)")
        await pool.execute("INSERT INTO ro349_probe VALUES (1)")
        rows = await sql_query(pool, SQLQuery(tenant_id=TENANT, sql="SELECT id FROM ro349_probe"))
        assert [row["id"] for row in rows] == [1]
        for sql in (
            DELETE_PLAIN.replace("documents", "ro349_probe"),
            DELETE_CTE,
            TWO_STATEMENTS.replace("tags", "ro349_probe"),
        ):
            with pytest.raises(WriteRefusedError):
                await sql_query(pool, SQLQuery(tenant_id=TENANT, sql=sql))
        # The transaction net: a write the text gate cannot see dies in the
        # READ ONLY transaction, and the row count stays.
        async with tenant_connection(pool, TENANT, readonly=True) as conn:
            with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                await conn.execute("DELETE FROM ro349_probe")
        count = await sql_query(
            pool, SQLQuery(tenant_id=TENANT, sql="SELECT count(*) AS n FROM ro349_probe")
        )
        assert count[0]["n"] == 1
    finally:
        await pool.close()
        await research_db.drop_research_db()
