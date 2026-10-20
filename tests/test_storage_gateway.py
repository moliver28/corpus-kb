"""Gateway seam tests (storage portability program, issue #127).

Hermetic DuckDB legs cover the protocol round-trip, PG-format status-tag
synthesis, conflict replay, statement-failure semantics, scope isolation,
batch writes, DSN helpers, and connect-error mapping. The PostgresGateway
legs run against asyncpg fakes, so no live server is needed. Grounded in
the W0 findings (findings/storage-ops.md, findings/sql-typing.md).
"""

from __future__ import annotations

import asyncio
import importlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from corpus_kb._setup.schema_apply import packaged_schema_path  # noqa: E402
from corpus_kb.errors import (  # noqa: E402
    ConflictError,
    DatabaseUnavailableError,
    ValidationError,
)
from corpus_kb.storage.gateway import (  # noqa: E402
    APPEND_ONLY_TABLES,
    DuckDbGateway,
    PostgresGateway,
    SqlGateway,
    duckdb_conn_string_to_path,
    duckdb_path_to_conn_string,
    map_connect_error,
)
from tests.mock_pg import FakeSqlGateway, transaction_cm  # noqa: E402

LOCKED_REMEDIATION = "Another program is using this database file. Close it, then try again."


# ---------------------------------------------------------------------------
# DSN helpers (rule 13: drive-letter test at birth).
# ---------------------------------------------------------------------------


def test_dsn_round_trip_windows_drive_letter_and_backslashes() -> None:
    dsn = duckdb_path_to_conn_string("C:\\data\\corpus.duckdb")
    assert dsn == "duckdb:///C:/data/corpus.duckdb"
    assert duckdb_conn_string_to_path(dsn) == "C:/data/corpus.duckdb"


def test_dsn_round_trip_posix_absolute_path() -> None:
    dsn = duckdb_path_to_conn_string("/tmp/corpora/kb.duckdb")
    assert dsn == "duckdb:////tmp/corpora/kb.duckdb"
    assert duckdb_conn_string_to_path(dsn) == "/tmp/corpora/kb.duckdb"


def test_dsn_round_trip_with_spaces_and_percent_escapes() -> None:
    dsn = duckdb_path_to_conn_string("F:/my corpora/a b.duckdb")
    assert duckdb_conn_string_to_path(dsn) == "F:/my corpora/a b.duckdb"
    assert duckdb_conn_string_to_path("duckdb:///F:/my%20corpora/a.duckdb") == (
        "F:/my corpora/a.duckdb"
    )


def test_dsn_inverse_is_idempotent_and_passes_plain_paths_through() -> None:
    once = duckdb_conn_string_to_path("duckdb:///C:/data/x.duckdb")
    assert duckdb_conn_string_to_path(once) == once
    assert duckdb_conn_string_to_path("C:/plain/path.duckdb") == "C:/plain/path.duckdb"


def test_dsn_inverse_strips_the_leading_slash_before_memory() -> None:
    """``duckdb:///:memory:`` must land as ``:memory:``, never a cwd file.

    Without the strip, Windows joins the leaked leading slash onto the
    current drive (``F:\\:memory:``) and the connect fails with a syntax
    error. Plain ``:memory:`` passes through untouched.
    """
    assert duckdb_conn_string_to_path("duckdb:///:memory:") == ":memory:"
    assert duckdb_conn_string_to_path(":memory:") == ":memory:"


def test_dsn_inverse_rejects_foreign_schemes() -> None:
    with pytest.raises(ValueError, match="scheme"):
        duckdb_conn_string_to_path("postgresql://corpus_user@localhost/corpus_kb")


async def test_connect_accepts_a_duckdb_dsn(tmp_path: Path) -> None:
    gw = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(tmp_path / "dsn.duckdb")))
    try:
        assert await gw.fetchval("SELECT 41 + 1") == 42
    finally:
        await gw.close()


# ---------------------------------------------------------------------------
# Connect-time configuration (W0 spike 7).
# ---------------------------------------------------------------------------


async def test_connect_sets_pg_null_order_and_disables_progress_bar(
    duckdb_path: Path,
) -> None:
    gw = await DuckDbGateway.connect(str(duckdb_path))
    try:
        assert await gw.fetchval("SELECT current_setting('default_null_order')") == (
            "NULLS_LAST_ON_ASC_FIRST_ON_DESC"
        )
        assert await gw.fetchval("SELECT current_setting('enable_progress_bar')") is False
    finally:
        await gw.close()


async def test_connect_applies_extra_config_threads(duckdb_path: Path) -> None:
    gw = await DuckDbGateway.connect(str(duckdb_path), config={"threads": 2})
    try:
        assert await gw.fetchval("SELECT current_setting('threads')") == 2
    finally:
        await gw.close()


# ---------------------------------------------------------------------------
# Protocol round-trip and the Row adapter.
# ---------------------------------------------------------------------------


async def test_protocol_round_trip(duckdb_gateway: DuckDbGateway) -> None:
    gw = duckdb_gateway
    assert isinstance(gw, SqlGateway)
    await gw.execute("CREATE TABLE t (k VARCHAR, v INT)")
    assert await gw.execute("INSERT INTO t VALUES ($1, $2)", "a", 1) == "INSERT 0 1"
    rows = await gw.fetch("SELECT k, v FROM t ORDER BY k")
    assert [dict(r) for r in rows] == [{"k": "a", "v": 1}]
    row = await gw.fetchrow("SELECT k, v FROM t")
    assert row is not None
    assert row["k"] == "a" and row["v"] == 1  # key access
    assert row[0] == "a" and row[1] == 1  # positional access
    assert await gw.fetchval("SELECT v FROM t WHERE k = $1", "a") == 1
    assert await gw.fetchval("SELECT v FROM t WHERE k = 'missing'") is None
    assert await gw.fetchrow("SELECT k FROM t WHERE k = 'missing'") is None
    assert await gw.fetch("SELECT k FROM t WHERE k = 'missing'") == []


async def test_transaction_commits_and_rolls_back(duckdb_gateway: DuckDbGateway) -> None:
    gw = duckdb_gateway
    await gw.execute("CREATE TABLE t (v INT)")
    async with gw.transaction("tenant-a"):
        await gw.execute("INSERT INTO t VALUES (1)")
    assert await gw.fetchval("SELECT count(*) FROM t") == 1
    with pytest.raises(RuntimeError, match="boom"):
        async with gw.transaction("tenant-a"):
            await gw.execute("INSERT INTO t VALUES (2)")
            raise RuntimeError("boom")
    assert await gw.fetchval("SELECT count(*) FROM t") == 1


# ---------------------------------------------------------------------------
# PG-format status tags (parsed by projections/research/_common.py).
# ---------------------------------------------------------------------------


async def test_status_tags_match_postgres_shapes(duckdb_gateway: DuckDbGateway) -> None:
    gw = duckdb_gateway
    assert await gw.execute("CREATE TABLE t (v INT)") == "CREATE TABLE"
    assert await gw.execute("INSERT INTO t VALUES (1), (2)") == "INSERT 0 2"
    assert await gw.execute("UPDATE t SET v = 9 WHERE v = 1") == "UPDATE 1"
    assert await gw.execute("UPDATE t SET v = 9 WHERE v = 12345") == "UPDATE 0"
    assert await gw.execute("DELETE FROM t WHERE v = 9") == "DELETE 1"
    assert await gw.execute("INSERT INTO t VALUES (5) RETURNING v") == "INSERT 0 1"
    assert (await gw.execute("SET threads = 2")).startswith("SET")


# ---------------------------------------------------------------------------
# Batch writes (W0: one list param per array value; chunked multi-row VALUES).
# ---------------------------------------------------------------------------


async def test_write_batch_inserts_rows_with_array_values(duckdb_gateway: DuckDbGateway) -> None:
    gw = duckdb_gateway
    await gw.execute("CREATE TABLE b (tenant_id VARCHAR, f FLOAT[3], label VARCHAR)")
    rows = [
        {"tenant_id": "t", "f": [float(i), float(i + 1), float(i + 2)], "label": f"r{i}"}
        for i in range(5)
    ]
    await gw.write_batch("b", rows, chunk_rows=2)
    assert await gw.fetchval("SELECT count(*) FROM b") == 5
    vector = await gw.fetchval("SELECT f FROM b WHERE label = 'r4'")
    assert list(vector) == [4.0, 5.0, 6.0]
    assert await gw.fetchval("SELECT tenant_id FROM b WHERE label = 'r0'") == "t"


async def test_write_batch_ignores_empty_input(duckdb_gateway: DuckDbGateway) -> None:
    await duckdb_gateway.write_batch("no_such_table", [])


# ---------------------------------------------------------------------------
# Statement-failure semantics inside transactions (W0 sql-typing spike 2).
# ---------------------------------------------------------------------------


async def test_parser_error_leaves_transaction_usable(duckdb_gateway: DuckDbGateway) -> None:
    gw = duckdb_gateway
    await gw.execute("CREATE TABLE t (v INT)")
    async with gw.transaction("tenant-a"):
        with pytest.raises(duckdb.Error, match="syntax error"):
            await gw.fetchval("SELEC 1")
        await gw.execute("INSERT INTO t VALUES (7)")
    assert await gw.fetchval("SELECT count(*) FROM t") == 1


async def test_nested_transaction_is_a_no_op(duckdb_gateway: DuckDbGateway) -> None:
    gw = duckdb_gateway
    await gw.execute("CREATE TABLE t (v INT)")
    async with gw.transaction("tenant-a"):
        await gw.execute("INSERT INTO t VALUES (1)")
        with pytest.raises(duckdb.Error, match="syntax error"):
            async with gw.transaction("tenant-a"):
                await gw.fetchval("SELEC 1")
        await gw.execute("INSERT INTO t VALUES (2)")
    assert await gw.fetchval("SELECT count(*) FROM t") == 2


async def test_conversion_error_aborts_and_surfaces_faithfully(
    duckdb_gateway: DuckDbGateway,
) -> None:
    gw = duckdb_gateway
    await gw.execute("CREATE TABLE t (v INT)")
    with pytest.raises(duckdb.TransactionException, match="aborted"):
        async with gw.transaction("tenant-a"):
            await gw.execute("INSERT INTO t VALUES (1)")
            with pytest.raises(duckdb.ConversionException):
                await gw.fetchval("SELECT CAST('abc' AS INT)")
            # The aborted state surfaces on the next statement; the gateway
            # must not swallow it. The outer scope rolls back.
            await gw.fetchval("SELECT 1")
    assert await gw.fetchval("SELECT count(*) FROM t") == 0


# ---------------------------------------------------------------------------
# Scope isolation: one connection, concurrent scopes never interleave.
# ---------------------------------------------------------------------------


async def test_statement_from_another_task_waits_for_the_tx_scope(
    duckdb_gateway: DuckDbGateway,
) -> None:
    gw = duckdb_gateway
    await gw.execute("CREATE TABLE t (v INT)")
    async with gw.transaction("tenant-a"):
        await gw.execute("INSERT INTO t VALUES (1)")
        parked = asyncio.create_task(gw.fetchval("SELECT count(*) FROM t"))
        for _ in range(20):
            await asyncio.sleep(0.01)
            if parked.done():  # pragma: no cover - failure diagnostics only
                break
        assert not parked.done()
        assert await gw.fetchval("SELECT count(*) FROM t") == 1
    assert await parked == 1


# ---------------------------------------------------------------------------
# Write-write conflict: replay the whole unit (W0 storage-ops spike 3).
# ---------------------------------------------------------------------------


async def test_conflict_retries_the_whole_unit(tmp_path: Path) -> None:
    path = str(tmp_path / "conflict.duckdb")
    gw = await DuckDbGateway.connect(path)
    writer = duckdb.connect(path, config={"default_null_order": "postgres"})
    try:
        await gw.execute("CREATE TABLE m (k INT, v INT)")
        await gw.execute("INSERT INTO m VALUES (1, 100)")
        writer.execute("BEGIN TRANSACTION")
        writer.execute("UPDATE m SET v = 1 WHERE k = 1")  # uncommitted rival version
        attempts: list[int] = []

        async def body(tx: SqlGateway) -> int:
            attempts.append(1)
            if len(attempts) >= 2:
                writer.execute("ROLLBACK")  # release the row before the replay
            await tx.execute("UPDATE m SET v = v + 1 WHERE k = 1")
            return len(attempts)

        assert await gw.run_transaction("tenant-a", body) == 2
        assert await gw.fetchval("SELECT v FROM m WHERE k = 1") == 101
    finally:
        writer.close()
        await gw.close()


async def test_conflict_from_a_plain_transaction_propagates(tmp_path: Path) -> None:
    path = str(tmp_path / "conflict2.duckdb")
    gw = await DuckDbGateway.connect(path)
    writer = duckdb.connect(path, config={"default_null_order": "postgres"})
    try:
        await gw.execute("CREATE TABLE m (k INT, v INT)")
        await gw.execute("INSERT INTO m VALUES (1, 100)")
        writer.execute("BEGIN TRANSACTION")
        writer.execute("UPDATE m SET v = 1 WHERE k = 1")
        with pytest.raises(duckdb.TransactionException, match="Conflict on update!"):
            async with gw.transaction("tenant-a"):
                await gw.execute("UPDATE m SET v = 2 WHERE k = 1")
        writer.execute("ROLLBACK")
        assert await gw.fetchval("SELECT v FROM m WHERE k = 1") == 100
    finally:
        writer.close()
        await gw.close()


# ---------------------------------------------------------------------------
# Connect-error mapping (rule 11: existing taxonomy + verbatim bank string).
# ---------------------------------------------------------------------------


async def test_read_only_missing_file_maps_to_database_unavailable(tmp_path: Path) -> None:
    with pytest.raises(DatabaseUnavailableError):
        await DuckDbGateway.connect(str(tmp_path / "missing.duckdb"), read_only=True)


async def test_config_mismatch_second_connection_maps_to_database_unavailable(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "cfg.duckdb")
    gw = await DuckDbGateway.connect(path)
    try:
        with pytest.raises(DatabaseUnavailableError):
            await DuckDbGateway.connect(path, config={"threads": 2})
    finally:
        await gw.close()


@pytest.mark.parametrize(
    ("exc_type", "message"),
    [
        (
            duckdb.IOException,
            'IO Error: Cannot open file "x.duckdb": The process cannot access the '
            "file because it is being used by another process.\n"
            "File is already open in\nC:\\python.exe (PID 42064)",
        ),
        (
            duckdb.IOException,
            'IO Error: Cannot open file "x.duckdb". File is already open in\nx (PID 1)',
        ),
        (
            duckdb.IOException,
            'IO Error: Cannot open database "x.duckdb" in read-only mode: database does not exist',
        ),
        (
            duckdb.ConnectionException,
            "Connection Error: Can't open a connection to same database file with "
            "a different configuration than existing connections",
        ),
    ],
)
def test_map_connect_error_bank_strings(exc_type: type[Exception], message: str) -> None:
    mapped = map_connect_error(exc_type(message))
    assert isinstance(mapped, DatabaseUnavailableError)
    assert mapped.remediation == LOCKED_REMEDIATION


def test_map_connect_error_passes_other_errors_through() -> None:
    plain = duckdb.ParserException("Parser Error: nope")
    assert map_connect_error(plain) is plain


# ---------------------------------------------------------------------------
# Append-only enforcement (gateway-level; PG keeps its triggers).
# ---------------------------------------------------------------------------


async def test_append_only_tables_reject_update_and_delete(duckdb_gateway: DuckDbGateway) -> None:
    assert "research_exact_cache" in APPEND_ONLY_TABLES
    for sql in (
        "UPDATE research_exact_cache SET hit_count = 2",
        "DELETE FROM chunk_signals",
    ):
        with pytest.raises(ConflictError):
            await duckdb_gateway.execute(sql)


# ---------------------------------------------------------------------------
# corpus.-qualification guard (Wave B blocker fix).
#
# DuckDB names a catalog after the database file stem, so
# ``corpus.documents`` silently works on a file named corpus.duckdb and
# fails with a raw catalog error on any other name. The gateway rejects
# qualified SQL with a typed error BEFORE executing; quoted regions are
# masked first, so literal data can neither trip nor dodge the guard.
# ---------------------------------------------------------------------------


CORPUS_QUALIFIED_MESSAGE = "Use unqualified table names on DuckDB. Remove the corpus. prefix."


async def test_corpus_qualified_sql_raises_the_typed_error_on_any_filename(
    tmp_path: Path,
) -> None:
    # corpus.duckdb is the file-stem collision case; kb.duckdb is the raw
    # catalog-error case. The typed rejection must not depend on the name.
    for name in ("corpus.duckdb", "kb.duckdb"):
        gw = await DuckDbGateway.connect(str(tmp_path / name))
        try:
            with pytest.raises(ValidationError) as excinfo:
                await gw.fetch("SELECT * FROM corpus.documents LIMIT 1")
            assert str(excinfo.value) == CORPUS_QUALIFIED_MESSAGE
            assert excinfo.value.error_type == "validation_error"
            assert excinfo.value.remediation
            for sql in (
                "SELECT * FROM CORPUS.documents LIMIT 1",  # case-insensitive
                "SELECT * FROM corpus . documents LIMIT 1",  # spaced dot
                'SELECT * FROM corpus."documents" LIMIT 1',  # quoted table part
            ):
                with pytest.raises(ValidationError, match=re.escape(CORPUS_QUALIFIED_MESSAGE)):
                    await gw.fetchrow(sql)
                with pytest.raises(ValidationError, match=re.escape(CORPUS_QUALIFIED_MESSAGE)):
                    await gw.execute(sql)
        finally:
            await gw.close()


async def test_corpus_token_inside_quoted_regions_is_untouched(
    duckdb_gateway: DuckDbGateway,
) -> None:
    gw = duckdb_gateway
    # A string literal containing the token must pass through verbatim.
    assert await gw.fetchval("SELECT 'corpus.x' AS t") == "corpus.x"
    # A double-quoted identifier is opaque too.
    row = await gw.fetchrow('SELECT 42 AS "corpus.c"')
    assert row is not None and row["corpus.c"] == 42


async def test_escaped_and_unbalanced_quotes_do_not_confuse_the_mask(
    duckdb_gateway: DuckDbGateway,
) -> None:
    gw = duckdb_gateway
    # A doubled quote is an escape inside one literal, never close+open:
    # the token stays quoted and must reach DuckDB untouched.
    assert await gw.fetchval("SELECT 'x''corpus.x''y' AS t") == "x'corpus.x'y"
    # An unterminated literal must not hang or crash the scanner; DuckDB
    # receives the statement and raises its own parser error (not ours).
    with pytest.raises(duckdb.Error):
        await gw.fetch("SELECT 'unclosed corpus.x")


async def test_unqualified_names_work_on_a_migrated_scratch_db(
    duckdb_gateway: DuckDbGateway,
) -> None:
    # The DuckDB migration mirror flattens every table to main, so ported
    # call sites pass bare names. Until sp-duckdb-migrations lands, a
    # scratch table stands in for the migrated schema.
    gw = duckdb_gateway
    await gw.execute("CREATE TABLE documents (chunk_id VARCHAR, title VARCHAR)")
    await gw.execute("INSERT INTO documents VALUES ('c1', 't1')")
    rows = await gw.fetch("SELECT * FROM documents ORDER BY chunk_id LIMIT 1")
    assert [dict(r) for r in rows] == [{"chunk_id": "c1", "title": "t1"}]


# ---------------------------------------------------------------------------
# PostgresGateway: thin wrapper, byte-identical tags, tenant GUC.
# ---------------------------------------------------------------------------


class _FakePgConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self._tx = transaction_cm()

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(("fetch", sql, args))
        return [{"v": 1}]

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        self.calls.append(("fetchrow", sql, args))
        return {"v": 1}

    async def fetchval(self, sql: str, *args: Any) -> int:
        self.calls.append(("fetchval", sql, args))
        return 7

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append(("execute", sql, args))
        return "UPDATE 3"

    def transaction(self) -> Any:
        return self._tx


class _FakePgPool:
    def __init__(self) -> None:
        self.conn = _FakePgConn()

    def acquire(self) -> Any:
        pool = self

        @asynccontextmanager
        async def _acquire() -> AsyncIterator[_FakePgConn]:
            yield pool.conn

        return _acquire()


async def test_postgres_gateway_passthrough_and_status_tags_unchanged() -> None:
    pool = _FakePgPool()
    gw = PostgresGateway(pool)
    assert isinstance(gw, SqlGateway)
    assert await gw.execute("UPDATE x SET y = 1") == "UPDATE 3"
    assert await gw.fetchval("SELECT 7") == 7
    rows = await gw.fetch("SELECT v FROM t", 5, "a")
    assert rows == [{"v": 1}]
    assert pool.conn.calls[-1] == ("fetch", "SELECT v FROM t", (5, "a"))


async def test_postgres_gateway_transaction_sets_tenant_guc() -> None:
    pool = _FakePgPool()
    gw = PostgresGateway(pool)
    async with gw.transaction("tenant-9") as tx:
        assert tx is not None
        await tx.fetch("SELECT 1")
        with pytest.raises(RuntimeError, match="boom"):
            async with tx.transaction("tenant-9"):
                raise RuntimeError("boom")
    guc_calls = [c for c in pool.conn.calls if "set_config" in c[1]]
    assert guc_calls == [
        ("execute", "SELECT set_config('app.current_tenant_id', $1, true)", ("tenant-9",))
    ]
    # __aexit__ calls: [nested savepoint (RuntimeError), outer scope (clean)].
    exits = pool.conn._tx.__aexit__.call_args_list
    assert exits[0][0][0] is RuntimeError
    assert exits[1][0][0] is None


# ---------------------------------------------------------------------------
# FakeSqlGateway birth (program rule 19).
# ---------------------------------------------------------------------------


def test_fake_gateway_satisfies_the_protocol() -> None:
    assert isinstance(FakeSqlGateway(), SqlGateway)


async def test_fake_gateway_canned_rows_calls_and_tags() -> None:
    fake = FakeSqlGateway(
        {
            "SELECT v FROM t": [{"v": 1}, {"v": 2}],
            "UPDATE x SET y = 5": "UPDATE 7",
        }
    )
    assert [r["v"] for r in await fake.fetch("SELECT v FROM t WHERE k = $1", "a")] == [1, 2]
    row = await fake.fetchrow("SELECT v FROM t")
    assert row is not None and row["v"] == 1
    assert await fake.fetchval("SELECT v FROM missing_t") is None
    assert await fake.fetch("SELECT v FROM missing_t") == []
    assert await fake.execute("UPDATE x SET y = 5") == "UPDATE 7"
    assert await fake.execute("INSERT INTO t VALUES (1)") == "INSERT 0 1"
    assert await fake.execute("DELETE FROM t") == "DELETE 1"
    assert fake.calls[0] == ("SELECT v FROM t WHERE k = $1", ("a",))


async def test_fake_gateway_transaction_counters() -> None:
    fake = FakeSqlGateway()
    async with fake.transaction("t1"):
        async with fake.transaction("t1"):
            pass
        assert fake.tx_depth == 1
    assert (fake.commits, fake.rollbacks) == (1, 0)
    with pytest.raises(ValueError):
        async with fake.transaction("t1"):
            raise ValueError
    assert (fake.commits, fake.rollbacks) == (1, 1)
    assert fake.tenant_ids == ["t1", "t1", "t1"]


# ---------------------------------------------------------------------------
# Backend-parametrized conftest fixture (rules 21/23) — smoke leg.
# ---------------------------------------------------------------------------


async def test_backend_parametrized_fixture_yields_protocol_gateways(
    any_gateway: SqlGateway,
) -> None:
    assert isinstance(any_gateway, SqlGateway)
    assert await any_gateway.fetchval("SELECT 1 + 1") == 2


# ---------------------------------------------------------------------------
# research_db guard (rule 22): schema.sql must stay strip-free.
# ---------------------------------------------------------------------------


def test_research_db_applies_schema_verbatim_no_hnsw_strip_needed() -> None:
    schema = packaged_schema_path().read_text(encoding="utf-8")
    # Only active DDL is applied; "--" comment lines are stripped first.
    active = "\n".join(line for line in schema.splitlines() if not line.lstrip().startswith("--"))
    hnsw_fragments = [
        fragment.strip() for fragment in re.findall(r"USING hnsw \(([^)]*)\)", active) if fragment
    ]
    assert hnsw_fragments, "schema.sql lost its hnsw index"
    for fragment in hnsw_fragments:
        assert "halfvec" in fragment, (
            "research_db applies schema.sql verbatim; a plain-vector hnsw index "
            "would break the session fixture (rule 22)"
        )


@pytest.mark.requires_postgres
async def test_applied_research_schema_keeps_hnsw_indexes(research_dsn: str) -> None:
    """Rule 22 pin, applied half: the live scratch DB keeps hnsw indexes.

    Needs a live Postgres, so it is gated by the existing requires_postgres
    marker (auto-skip locally without Postgres; the ubuntu CI service leg
    from phase B burns it in with the other live-PG marks).
    """
    import asyncpg

    conn = await asyncpg.connect(research_dsn)
    try:
        rows = await conn.fetch(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND indexdef ILIKE '%using hnsw%' ORDER BY indexdef"
        )
    finally:
        await conn.close()
    defs = [row["indexdef"] for row in rows]
    assert defs, "applied research schema lost its hnsw indexes (rule 22)"
    # Migration 010 adds a legitimate plain-vector hnsw index, so "all
    # halfvec" is wrong; the pin is that the halfvec fragment the session
    # fixture relies on survives application.
    assert any("halfvec" in d for d in defs), (
        f"applied research schema lost the halfvec hnsw index (rule 22); applied: {defs}"
    )


# ---------------------------------------------------------------------------
# research_db DSN is env-derived with the local fallback preserved (rule 20).
# ---------------------------------------------------------------------------


def test_research_db_dsn_is_env_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "pg.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_USER", "svc")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("POSTGRES_SUPERUSER", "root")
    monkeypatch.setenv("POSTGRES_SUPERUSER_PASSWORD", "rootpw")
    module = importlib.reload(importlib.import_module("research_db"))
    assert module.BASE_DSN == "postgresql://svc:pw@pg.internal:6543"
    # Admin DSNs name the postgres maintenance database explicitly: asyncpg
    # defaults a database-less DSN to the USER name, which only resolves on
    # hosts where a database named after the role exists.
    assert module.SUPER_DSN == "postgresql://root:rootpw@pg.internal:6543/postgres"
    assert module.super_dsn_for("scratch") == "postgresql://root:rootpw@pg.internal:6543/scratch"
    assert f"{module.BASE_DSN}/{module.DB_NAME}" == module.DSN

    for name in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_SUPERUSER",
        "POSTGRES_SUPERUSER_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    fallback = importlib.reload(importlib.import_module("research_db"))
    assert fallback.BASE_DSN == "postgresql://corpus_user:corpus_pass@localhost:5432"
    assert fallback.SUPER_DSN == "postgresql://postgres:postgres@localhost:5432/postgres"
