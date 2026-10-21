"""DuckDB storage checks for ``corpus-kb doctor`` (storage portability §9).

Hermetic: real duckdb in-memory/tmp-file connections, real sqlite3 tmp
sidecars, no network (the extension probe tests use doubles).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pytest

from corpus_kb._setup.doctor_duckdb import (
    duckdb_lock_check,
    extension_load_check,
    fts_watermark_check,
)
from corpus_kb._setup.doctor_engine import engine_fit_check, resolved_backend
from corpus_kb._setup.doctor_research import (
    STATUS_FAIL,
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
)
from corpus_kb._setup.doctor_storage import events_sidecar_check, storage_checks
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string


class _Probe:
    """Connection double whose ``execute`` fails for the named statements."""

    def __init__(self, fail: set[str]) -> None:
        self.fail = fail

    def execute(self, sql: str) -> str:
        for name in self.fail:
            if f"'{name}'" in sql:
                raise RuntimeError(f"Extension {name} not installed")
        return "OK"


def _chunks_con(rows: int, docs: int | None) -> duckdb.DuckDBPyConnection:
    """Memory connection with a chunks table and a faked fts docs table."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE chunks (chunk_id VARCHAR, tenant_id VARCHAR, text VARCHAR)")
    for i in range(rows):
        con.execute("INSERT INTO chunks VALUES (?, 't', ?)", [f"c{i}", f"text {i}"])
    if docs is not None:
        con.execute("CREATE SCHEMA fts_main_chunks")
        con.execute("CREATE TABLE fts_main_chunks.docs (docname VARCHAR)")
        for i in range(docs):
            con.execute("INSERT INTO fts_main_chunks.docs VALUES (?)", [f"c{i}"])
    return con


# --- lock check ---------------------------------------------------------------


def test_lock_check_ok_when_file_opens(tmp_path: Path) -> None:
    path = tmp_path / "kb.duckdb"
    duckdb.connect(str(path)).close()
    check = duckdb_lock_check(str(path))
    assert check.status == STATUS_OK


def test_lock_check_warns_when_file_missing(tmp_path: Path) -> None:
    check = duckdb_lock_check(str(tmp_path / "absent.duckdb"))
    assert check.status == STATUS_WARN
    assert "setup" in check.fix


def test_lock_check_fails_with_holder_when_locked(tmp_path: Path) -> None:
    path = tmp_path / "kb.duckdb"
    duckdb.connect(str(path)).close()

    def locked(_path: str) -> None:
        raise duckdb.IOException(
            f"Could not set lock on file: {path} being used by another process"
        )

    check = duckdb_lock_check(str(path), opener=locked)
    assert check.status == STATUS_FAIL
    assert "one process" in check.detail.lower()
    assert "server" in check.detail.lower()


def test_lock_check_reports_unopenable_file(tmp_path: Path) -> None:
    path = tmp_path / "kb.duckdb"
    path.write_text("not a database", encoding="utf-8")

    def broken(_path: str) -> None:
        raise RuntimeError("invalid input syntax")

    check = duckdb_lock_check(str(path), opener=broken)
    assert check.status == STATUS_FAIL
    assert "DuckDB database" in check.fix


# --- fts watermark --------------------------------------------------------------


def test_fts_watermark_ok_when_counts_match() -> None:
    con = _chunks_con(rows=3, docs=3)
    check = fts_watermark_check(con)
    assert check.status == STATUS_OK
    assert "3" in check.detail


def test_fts_watermark_warns_when_index_stale() -> None:
    con = _chunks_con(rows=5, docs=3)
    check = fts_watermark_check(con)
    assert check.status == STATUS_WARN
    assert "stale" in check.detail.lower()
    assert "search" in check.fix.lower()


def test_fts_watermark_warns_when_index_missing() -> None:
    con = _chunks_con(rows=3, docs=None)
    check = fts_watermark_check(con)
    assert check.status == STATUS_WARN
    assert "missing" in check.detail.lower()


def test_fts_watermark_warns_when_chunks_missing() -> None:
    con = duckdb.connect(":memory:")
    check = fts_watermark_check(con)
    assert check.status == STATUS_WARN
    assert "migration" in check.detail.lower() or "setup" in check.fix.lower()


# --- extension LOAD probes -------------------------------------------------------


def test_extension_probes_all_ok() -> None:
    checks = extension_load_check(_Probe(fail=set()))
    assert [c.status for c in checks] == [STATUS_OK, STATUS_OK, STATUS_OK]


def test_extension_probe_fts_missing_is_warn_with_vendoring() -> None:
    checks = extension_load_check(_Probe(fail={"fts"}))
    fts = checks[0]
    assert fts.status == STATUS_WARN
    assert "extension_directory" in fts.fix


def test_extension_probe_optional_missing_is_info() -> None:
    checks = extension_load_check(_Probe(fail={"vss", "postgres"}))
    assert [c.status for c in checks[1:]] == [STATUS_INFO, STATUS_INFO]
    assert all("extension_directory" in c.fix for c in checks[1:])


# --- sqlite event-store sidecar --------------------------------------------------


def test_sidecar_check_healthy(tmp_path: Path) -> None:
    dsn = duckdb_path_to_conn_string(str(tmp_path / "kb.duckdb"))
    sidecar = str(tmp_path / "kb.duckdb.events.db")
    with sqlite3.connect(sidecar) as con:
        con.execute("CREATE TABLE stored_events (id INTEGER)")
        con.execute("INSERT INTO stored_events VALUES (1)")

    check = events_sidecar_check(dsn)
    assert check.status == STATUS_OK
    assert "1" in check.detail


def test_sidecar_check_absent_is_info(tmp_path: Path) -> None:
    dsn = duckdb_path_to_conn_string(str(tmp_path / "kb.duckdb"))
    check = events_sidecar_check(dsn)
    assert check.status == STATUS_INFO


def test_sidecar_check_schema_missing_is_warn(tmp_path: Path) -> None:
    dsn = duckdb_path_to_conn_string(str(tmp_path / "kb.duckdb"))
    (tmp_path / "kb.duckdb.events.db").write_bytes(b"")
    check = events_sidecar_check(dsn)
    assert check.status == STATUS_WARN


def test_sidecar_check_fails_when_dsn_names_sidecar(tmp_path: Path) -> None:
    dsn = duckdb_path_to_conn_string(str(tmp_path / "kb.duckdb.events.db"))
    check = events_sidecar_check(dsn)
    assert check.status == STATUS_FAIL
    assert "event" in check.detail.lower()


def test_sidecar_check_fails_on_unopenable_file(tmp_path: Path) -> None:
    dsn = duckdb_path_to_conn_string(str(tmp_path / "kb.duckdb"))
    (tmp_path / "kb.duckdb.events.db").write_bytes(b"\xff\xff not sqlite")
    check = events_sidecar_check(dsn)
    assert check.status == STATUS_FAIL


# --- engine fit ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend", "count", "expected"),
    [
        ("duckdb", None, STATUS_INFO),
        ("duckdb", 50_000, STATUS_INFO),
        ("duckdb", 250_000, STATUS_INFO),
        ("postgres", None, STATUS_INFO),
        ("postgres", 50_000, STATUS_INFO),
        ("postgres", 250_000, STATUS_INFO),
    ],
)
def test_engine_fit_is_never_a_warning(backend: str, count: int | None, expected: str) -> None:
    assert engine_fit_check(backend, count).status == expected


def test_engine_fit_duckdb_small_names_sweet_spot() -> None:
    detail = engine_fit_check("duckdb", 50_000).detail.lower()
    assert "duckdb" in detail
    assert "100" in detail  # the hedged ceiling appears once


def test_engine_fit_duckdb_large_suggests_postgres() -> None:
    detail = engine_fit_check("duckdb", 250_000).detail.lower()
    assert "postgres" in detail


def test_engine_fit_postgres_small_suggests_duckdb() -> None:
    detail = engine_fit_check("postgres", 50_000).detail.lower()
    assert "duckdb" in detail


# --- backend resolution + assembly ------------------------------------------------


def test_resolved_backend_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORPUS_KB_DATABASE_BACKEND", "duckdb")
    assert resolved_backend({"database": {"backend": "postgres"}}) == "duckdb"


def test_resolved_backend_defaults_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORPUS_KB_DATABASE_BACKEND", raising=False)
    assert resolved_backend({}) == "postgres"


@pytest.mark.asyncio
async def test_storage_checks_duckdb_full_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provisioned duckdb file: lock ok, fts reported, sidecar reported."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_BACKEND", raising=False)
    monkeypatch.setattr(  # the real probe downloads; the unit tests cover it
        "corpus_kb._setup.doctor_storage.extension_load_check", lambda _con: []
    )
    from corpus_kb._setup.migrate import run_duckdb_migrations

    db = tmp_path / "kb.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(db)))
    dsn = duckdb_path_to_conn_string(str(db))
    config = {"database": {"backend": "duckdb", "connection_string": dsn}}

    checks = await storage_checks(config, dsn, postgres_ok=False)
    names = {c.name for c in checks}
    assert "DuckDB file lock" in names
    assert "FTS index watermark" in names
    assert "SQLite event store" in names
    assert any(c.name == "engine fit" and c.status == STATUS_INFO for c in checks)


@pytest.mark.asyncio
async def test_storage_checks_duckdb_backend_without_duckdb_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORPUS_KB_DATABASE_BACKEND", raising=False)
    config = {
        "database": {
            "backend": "duckdb",
            "connection_string": "postgresql://u:p@localhost:5433/kb",
        }
    }
    checks = await storage_checks(config, config["database"]["connection_string"], False)
    assert any(c.status == STATUS_FAIL and "connection string" in c.detail for c in checks)


@pytest.mark.asyncio
async def test_storage_checks_postgres_reports_engine_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORPUS_KB_DATABASE_BACKEND", raising=False)
    checks = await storage_checks({"database": {"backend": "postgres"}}, "", False)
    assert len(checks) == 1
    assert checks[0].name == "engine fit"
    assert checks[0].status == STATUS_INFO


# --- doctor report section ---------------------------------------------------------


def _doctor_info() -> dict[str, object]:
    return {
        "os": "test",
        "python": "3.11",
        "cpu_cores": 1,
        "ram_gb": 1,
        "profile": "balanced",
        "postgres_ok": False,
        "postgres_msg": "x",
        "ollama_ok": False,
        "ollama_msg": "x",
        "recommended_model": "nomic-embed-text",
    }


def test_doctor_report_prints_storage_section(capsys: pytest.CaptureFixture[str]) -> None:
    from corpus_kb._setup.doctor_research import ResearchCheck
    from corpus_kb._setup.install import print_doctor_report

    storage = [ResearchCheck(name="engine fit", status="info", detail="Engine: Postgres.")]
    print_doctor_report(_doctor_info(), storage=storage)
    out = capsys.readouterr().out
    assert "Storage backend:" in out
    assert "engine fit: INFO" in out


def test_doctor_report_without_storage_omits_section(capsys: pytest.CaptureFixture[str]) -> None:
    from corpus_kb._setup.install import print_doctor_report

    print_doctor_report(_doctor_info())
    assert "Storage backend:" not in capsys.readouterr().out
