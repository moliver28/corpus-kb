"""The store-version boot guard (W5-a).

Contract: the server refuses to serve when the STORE schema is NEWER
than the CODE (typed ``StoreNewerError``, no partial serve). An older
store proceeds unchanged (migrations stay a setup/update step). A fresh
store — no tracking table — proceeds too.

Pure core (``check_store_not_newer``) plus one reader per engine and the
three serve paths that call the guard (http startup, duckdb startup,
stdio).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from corpus_kb._setup.version_guard import (
    StoreVersionReport,
    check_store_not_newer,
    migration_number,
)
from corpus_kb.errors import StoreNewerError

# ---------------------------------------------------------------------------
# The pure comparison
# ---------------------------------------------------------------------------


def test_migration_number_reads_the_numeric_prefix() -> None:
    assert migration_number("045_entity_id_aliases.sql") == 45
    assert migration_number("006_rrf_fusion.sql") == 6


def test_migration_number_answers_zero_for_junk() -> None:
    assert migration_number("rollback_006_rrf.sql") == 0
    assert migration_number("notes.txt") == 0


def test_store_at_code_head_passes() -> None:
    applied = ["001_a.sql", "044_b.sql", "045_entity_id_aliases.sql"]
    code = ["001_a.sql", "044_b.sql", "045_entity_id_aliases.sql"]
    report = check_store_not_newer(applied, code)
    assert isinstance(report, StoreVersionReport)
    assert report.store_head == "045_entity_id_aliases.sql"
    assert report.pending_count == 0


def test_store_older_than_code_passes_with_pending_count() -> None:
    report = check_store_not_newer(["001_a.sql"], ["001_a.sql", "044_b.sql", "045_c.sql"])
    assert report.pending_count == 2


def test_store_newer_than_code_raises_typed_error() -> None:
    applied = ["001_a.sql", "046_arrives_from_the_future.sql"]
    code = ["001_a.sql", "045_entity_id_aliases.sql"]
    with pytest.raises(StoreNewerError) as excinfo:
        check_store_not_newer(applied, code)
    message = str(excinfo.value)
    assert "046_arrives_from_the_future.sql" in message
    assert "045_entity_id_aliases.sql" in message


def test_store_newer_error_carries_the_conflict_triple() -> None:
    with pytest.raises(StoreNewerError) as excinfo:
        check_store_not_newer(["999_future.sql"], ["001_a.sql"])
    exc = excinfo.value
    assert (exc.http_status, exc.socket_code, exc.cli_exit) == (409, -32002, 3)


def test_remediation_names_update_and_restore() -> None:
    with pytest.raises(StoreNewerError) as excinfo:
        check_store_not_newer(["999_future.sql"], ["001_a.sql"])
    assert "Update" in excinfo.value.remediation


def test_fresh_store_passes() -> None:
    report = check_store_not_newer([], ["001_a.sql"])
    assert report.fresh is True
    assert report.pending_count == 1


# ---------------------------------------------------------------------------
# Engine readers
# ---------------------------------------------------------------------------


class _FakePgConn:
    """Stand-in for the guard's one asyncpg probe."""

    def __init__(self, rows: list[str] | Exception) -> None:
        self._rows = rows

    async def fetch(self, _sql: str) -> list[Any]:
        if isinstance(self._rows, Exception):
            raise self._rows
        return [{"filename": name} for name in self._rows]

    async def close(self) -> None:
        return None


async def test_pg_reader_answers_applied_names(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb._setup.version_guard as guard

    async def fake_connect(_dsn: str, **_kw: Any) -> _FakePgConn:
        return _FakePgConn(["001_a.sql", "045_entity_id_aliases.sql"])

    monkeypatch.setattr(guard.asyncpg, "connect", fake_connect)
    names = await guard.read_applied_pg("postgresql://stub")
    assert names == ["001_a.sql", "045_entity_id_aliases.sql"]


async def test_pg_reader_answers_none_on_a_fresh_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No tracking table means the migrations never ran: fresh, proceed."""
    import asyncpg

    import corpus_kb._setup.version_guard as guard

    async def fake_connect(_dsn: str, **_kw: Any) -> _FakePgConn:
        return _FakePgConn(asyncpg.UndefinedTableError("relation does not exist"))

    monkeypatch.setattr(guard.asyncpg, "connect", fake_connect)
    assert await guard.read_applied_pg("postgresql://stub") is None


async def test_pg_reader_lets_other_errors_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable database is not a fresh store: the error escapes."""
    import corpus_kb._setup.version_guard as guard

    async def refuse(_dsn: str, **_kw: Any) -> _FakePgConn:
        raise ConnectionError("refused")

    monkeypatch.setattr(guard.asyncpg, "connect", refuse)
    with pytest.raises(ConnectionError):
        await guard.read_applied_pg("postgresql://stub")


def _duckdb_file_with_rows(tmp_path: Path, rows: list[str]) -> str:
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "scratch.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE SCHEMA IF NOT EXISTS corpus;"
        "CREATE TABLE corpus.schema_migrations ("
        "filename VARCHAR PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())"
    )
    for name in rows:
        con.execute("INSERT INTO corpus.schema_migrations (filename) VALUES (?)", [name])
    con.close()
    return "duckdb:///" + path.as_posix()


async def test_duckdb_reader_answers_applied_names(tmp_path: Path) -> None:
    from corpus_kb._setup.version_guard import read_applied_duckdb

    dsn = _duckdb_file_with_rows(tmp_path, ["001_a.sql", "044_b.sql"])
    assert await read_applied_duckdb(dsn) == ["001_a.sql", "044_b.sql"]


async def test_duckdb_reader_answers_none_when_the_table_is_missing(
    tmp_path: Path,
) -> None:
    duckdb = pytest.importorskip("duckdb")
    from corpus_kb._setup.version_guard import read_applied_duckdb

    path = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plain (x INTEGER)")
    con.close()
    assert await read_applied_duckdb("duckdb:///" + path.as_posix()) is None


async def test_duckdb_reader_answers_none_when_the_file_is_missing(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    from corpus_kb._setup.version_guard import read_applied_duckdb

    missing = tmp_path / "never-created.duckdb"
    assert await read_applied_duckdb("duckdb:///" + missing.as_posix()) is None


async def test_dispatch_sends_duckdb_dsns_to_the_duckdb_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import corpus_kb._setup.version_guard as guard

    dsn = _duckdb_file_with_rows(tmp_path, ["001_a.sql"])
    calls: list[str] = []

    async def spy(_dsn: str) -> list[str]:
        calls.append(_dsn)
        return ["001_a.sql"]

    monkeypatch.setattr(guard, "read_applied_duckdb", spy)
    report = await guard.assert_store_supported(dsn, "duckdb")
    assert calls == [dsn]
    assert report is not None and report.store_head == "001_a.sql"


# ---------------------------------------------------------------------------
# The serve paths refuse before they wire anything
# ---------------------------------------------------------------------------


async def test_startup_refuses_a_newer_store_before_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb._setup.version_guard as guard
    from corpus_kb.server_wiring import startup

    async def refuse(_conn: str, _backend: str) -> StoreVersionReport:
        raise StoreNewerError("The store is newer than this code.")

    wired: list[str] = []

    async def must_not_run(*_a: Any, **_kw: Any) -> None:
        wired.append("pool")

    monkeypatch.setattr(guard, "assert_store_supported", refuse)
    monkeypatch.setattr("corpus_kb.server_wiring.initialize_postgres_pool", must_not_run)
    with pytest.raises(StoreNewerError):
        await startup(config={"database": {"connection_string": "postgresql://stub"}})
    assert wired == [], "the guard must refuse before the pool opens"


async def test_startup_older_store_proceeds_to_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old-store direction keeps today's behavior: boot continues."""
    import corpus_kb._setup.version_guard as guard
    from corpus_kb.errors import DatabaseUnavailableError
    from corpus_kb.server_wiring import startup

    report = StoreVersionReport(
        store_head="001_a.sql", code_head="045_c.sql", pending_count=2, fresh=False
    )

    async def fine(_conn: str, _backend: str) -> StoreVersionReport:
        return report

    async def stub_pool(_conn: str) -> None:
        raise DatabaseUnavailableError("stop here on purpose")

    monkeypatch.setattr(guard, "assert_store_supported", fine)
    monkeypatch.setattr("corpus_kb.server_wiring.initialize_postgres_pool", stub_pool)
    with pytest.raises(DatabaseUnavailableError):
        await startup(config={"database": {"connection_string": "postgresql://stub"}})


async def test_duckdb_startup_refuses_a_newer_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb._setup.version_guard as guard
    from corpus_kb.server_wiring_duckdb import startup_duckdb

    async def refuse(_dsn: str) -> StoreVersionReport:
        raise StoreNewerError("The store is newer than this code.")

    opened: list[str] = []

    async def must_not_open(*_a: Any, **_kw: Any) -> None:
        opened.append("gateway")

    monkeypatch.setattr(guard, "read_applied_duckdb", refuse)
    monkeypatch.setattr("corpus_kb.storage.gateway.DuckDbGateway.connect", must_not_open)
    with pytest.raises(StoreNewerError):
        await startup_duckdb({"database": {"backend": "duckdb"}}, "duckdb:///stub.duckdb")
    assert opened == [], "the guard must refuse before the gateway opens"


def test_stdio_refuses_a_newer_store_with_exit_three(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import corpus_kb._setup.version_guard as guard
    import corpus_kb.config as config_mod
    import corpus_kb.storage.connect as connect_mod
    from corpus_kb.mcp_server import run_stdio_server

    async def refuse(_conn: str, _backend: str) -> StoreVersionReport:
        raise StoreNewerError("The store is newer than this code.")

    monkeypatch.setattr(guard, "assert_store_supported", refuse)
    monkeypatch.setattr(config_mod, "load_config", lambda: {})
    monkeypatch.setattr(connect_mod, "config_conn_str", lambda _cfg: "postgresql://stub")
    assert run_stdio_server() == 3
    captured = capsys.readouterr().err
    assert "newer" in captured
