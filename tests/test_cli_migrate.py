"""Issue #348 scope 3: ``corpus-kb migrate``.

The DuckDB scratch file proves apply-once idempotence end to end with no
server. The Postgres leg (``requires_postgres``) replays the issue's
check on a scratch pre-043 database: 037..044 apply once, a second run
applies nothing, and the superuser handover leaves every table owned by
the app role.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from typer.testing import CliRunner

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.cli import app
from corpus_kb.errors import ConfigError

runner = CliRunner()


def _duckdb_dsn(tmp_path: Path) -> str:
    return "duckdb:///" + (tmp_path / "scratch.duckdb").as_posix()


async def test_duckdb_scratch_migrates_exactly_once(tmp_path: Path) -> None:
    dsn = _duckdb_dsn(tmp_path)
    first = await run_migrations(dsn)
    assert first.backend == "duckdb"
    assert first.pending, "a fresh file has pending migrations"
    assert first.applied_now == first.pending
    second = await run_migrations(dsn)
    assert second.pending == []
    assert second.applied_now == []


async def test_dry_run_applies_nothing(tmp_path: Path) -> None:
    dsn = _duckdb_dsn(tmp_path)
    plan = await run_migrations(dsn, dry_run=True)
    assert plan.dry_run is True
    assert plan.pending
    assert plan.applied_now == []
    real = await run_migrations(dsn)
    assert real.applied_now == plan.pending


def test_cli_migrate_dry_run_json(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)  # keep the repo config.yaml out of discovery
    result = runner.invoke(app, ["migrate", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["backend"] == "duckdb"
    assert report["dry_run"] is True
    assert report["pending"]


def test_cli_migrate_json_reports_applied(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["migrate", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["dry_run"] is False
    assert report["applied_now"] == report["pending"]


def test_cli_migrate_missing_overlay_exits_non_zero(tmp_path: Path, monkeypatch: Any) -> None:
    """The scope-2 Check: a missing overlay path exits non-zero."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    monkeypatch.setenv("CORPUS_KB_CONFIG_OVERLAY", str(tmp_path / "nope.yaml"))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["migrate", "--dry-run"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_migrate_rejects_bad_session_setting(tmp_path: Path, monkeypatch: Any) -> None:
    """Session settings reach SQL only through a strict allow-list."""
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["migrate", "--work-mem", "1GB; DROP SCHEMA public"])
    assert result.exit_code != 0
    assert "Invalid migration session setting" in result.output


# ---------------------------------------------------------------------------
# The human report and the honest exit codes (issue #348 adapter legs).
# ---------------------------------------------------------------------------


def test_cli_migrate_human_report_applies_then_answers_up_to_date(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The default report is human text; a second run says so honestly."""
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["migrate", "--maintenance-work-mem", "1MB"])
    assert result.exit_code == 0, result.output
    assert "Backend: duckdb" in result.output
    assert "Applied now:" in result.output

    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 0, result.output
    assert "Nothing to apply. The database is up to date." in result.output


def test_cli_migrate_dry_run_human_report_lists_pending(tmp_path: Path, monkeypatch: Any) -> None:
    """The human dry run lists the pending set and applies nothing."""
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["migrate", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Pending:" in result.output
    assert "Dry run. Nothing was applied." in result.output


def test_cli_migrate_without_any_database_exits_3(tmp_path: Path, monkeypatch: Any) -> None:
    """No env DSN and no configured string: one clear line, exit 3."""
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    import corpus_kb.config as config_mod

    monkeypatch.setattr(config_mod, "load_config", lambda: {"database": {}})
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 3
    assert "No database found" in result.output


def test_cli_migrate_typed_failure_maps_its_exit_code(tmp_path: Path, monkeypatch: Any) -> None:
    """A taxonomy failure from the runner exits with the taxonomy's code."""
    import corpus_kb._setup.migrate as migrate_mod
    from corpus_kb.errors import ConfigError

    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _duckdb_dsn(tmp_path))
    monkeypatch.chdir(tmp_path)

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise ConfigError("the scratch migration runner failed on purpose")

    monkeypatch.setattr(migrate_mod, "run_migrations", boom)
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 3
    assert "the scratch migration runner failed on purpose" in result.output


class _FakeGuardConn:
    """Stand-in for the guard's one probe: count of non-null old vectors."""

    def __init__(self, count: int) -> None:
        self._count = count

    async def fetchval(self, _sql: str) -> int:
        return self._count


async def test_025_guard_refuses_stored_vectors() -> None:
    """The 0.6.0 guard: refuse while the old vector column holds rows."""
    from corpus_kb._setup.migrate_admin import refuse_025_with_embeddings

    with pytest.raises(ConfigError) as excinfo:
        await refuse_025_with_embeddings(_FakeGuardConn(42), ["025_halfvec_2560.sql"], False)
    message = str(excinfo.value)
    assert "42" in message
    assert "--reset-embeddings" in message
    # No pending 025, an empty column, or --reset-embeddings all proceed.
    await refuse_025_with_embeddings(_FakeGuardConn(42), [], False)
    await refuse_025_with_embeddings(_FakeGuardConn(0), ["025_halfvec_2560.sql"], False)
    await refuse_025_with_embeddings(_FakeGuardConn(7), ["025_halfvec_2560.sql"], True)


class _NoOldColumnConn:
    """Guard probe double: the pre-025 table itself is gone."""

    def __init__(self) -> None:
        self.probes = 0

    async def fetchval(self, _sql: str) -> int:
        self.probes += 1
        raise asyncpg.PostgresError('relation "chunks_vectors" does not exist')


async def test_025_guard_probes_a_missing_table_silently() -> None:
    """No old vector table: the guard moves on instead of crashing."""
    from corpus_kb._setup.migrate_admin import refuse_025_with_embeddings

    conn = _NoOldColumnConn()
    await refuse_025_with_embeddings(conn, ["025_halfvec_2560.sql"], False)
    assert conn.probes == 1


class _RecordingAdminConn:
    """The superuser-connection contract of hand_over_to_app_role."""

    def __init__(self, su_oid: int | None, app_oid: int | None) -> None:
        self._oids = {"corpus_super": su_oid, "corpus_user": app_oid}
        self.executed: list[str] = []
        self.objects = [
            {"obj": "public.documents", "relkind": "r"},
            {"obj": "public.documents_doc_id_seq", "relkind": "S"},
            {"obj": "corpus.chunk_stats", "relkind": "v"},
        ]
        self.functions = [{"obj": "public.refresh_stats()"}]

    async def fetchval(self, sql: str, *args: object) -> int | None:
        if "pg_roles" in sql:
            return self._oids.get(str(args[0]))
        return None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        if "regclass" in sql:
            return list(self.objects)
        if "regprocedure" in sql:
            return list(self.functions)
        return []

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)


async def test_hand_over_moves_objects_and_grants() -> None:
    """Every object kind gets its ALTER verb; grants cover both schemas."""
    from corpus_kb._setup.migrate_admin import hand_over_to_app_role

    conn = _RecordingAdminConn(su_oid=10, app_oid=11)
    await hand_over_to_app_role(conn, "corpus_super", "corpus_user")
    verbs = [sql.split()[0] + " " + sql.split()[1] for sql in conn.executed]
    assert verbs[0] == "ALTER TABLE"
    assert verbs[1] == "ALTER SEQUENCE"
    assert verbs[2] == "ALTER VIEW"
    assert "ALTER FUNCTION public.refresh_stats() OWNER TO" in conn.executed[3]
    grants = [
        sql for sql in conn.executed if sql.startswith("GRANT") or "DEFAULT PRIVILEGES" in sql
    ]
    assert len(grants) == 10  # 2 schemas x (usage + tables + sequences + 2 defaults)
    assert 'GRANT USAGE ON SCHEMA "public" TO "corpus_user"' in conn.executed


async def test_hand_over_answers_silently_without_an_app_role() -> None:
    """An empty app role moves nothing and runs no SQL."""
    from corpus_kb._setup.migrate_admin import hand_over_to_app_role

    conn = _RecordingAdminConn(su_oid=10, app_oid=None)
    await hand_over_to_app_role(conn, "corpus_super", "")
    assert conn.executed == []


async def test_dsn_role_reads_the_user_and_survives_junk() -> None:
    """The DSN user drives the hand-over roles; junk answers empty."""
    from corpus_kb._setup.migrate_admin import dsn_role

    assert dsn_role("postgresql://corpus_user:pw@localhost:5432/corpus_kb") == "corpus_user"
    assert dsn_role("postgresql://corpus%2Bops:pw@localhost/db") == "corpus+ops"
    assert dsn_role("postgresql://localhost/db") == ""
    assert dsn_role("postgresql://[::1:5432/db") == ""  # unparseable bracket


def test_session_setting_names_are_strictly_shaped() -> None:
    """The allow-list refuses names and values outside its shape."""
    from corpus_kb._setup.migrate_admin import validate_session_settings

    validate_session_settings({"work_mem": "1GB"})  # no raise
    with pytest.raises(ConfigError):
        validate_session_settings({"work mem": "1GB"})
    with pytest.raises(ConfigError):
        validate_session_settings({"work_mem": "1GB; DROP SCHEMA"})


# ---------------------------------------------------------------------------
# Postgres leg: the issue's pre-043 scratch-database check.
# ---------------------------------------------------------------------------

#: Migrations above this number are the "pending" set of a pre-043 database.
_PRE_043 = 36


def _numbered_migrations() -> list[Path]:
    return sorted(
        p
        for p in (
            Path(__file__).resolve().parent.parent / "src" / "corpus_kb" / "migrations"
        ).iterdir()
        if p.is_file() and p.suffix == ".sql" and p.name[0].isdigit()
    )


async def _bootstrap_pre_043(db_name: str) -> str:
    """A scratch database at the pre-043 shape: schema + 001..036.

    The schema and the early migrations run as the APP role, so the
    app role owns the base tables exactly like a real study database;
    the superuser leg then only owns what IT creates (037+).
    """
    import asyncpg

    from corpus_kb._setup.migrate import ensure_migrations_table
    from corpus_kb._setup.schema_apply import EXTENSION_GATED_MIGRATIONS, packaged_schema_path
    from tests.research_db import BASE_DSN, drop_research_db, super_dsn_for

    await drop_research_db()
    admin = await asyncpg.connect(super_dsn_for("postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}" OWNER corpus_user')
    finally:
        await admin.close()
    superconn = await asyncpg.connect(super_dsn_for(db_name))
    try:
        await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    finally:
        await superconn.close()

    app_dsn = f"{BASE_DSN}/{db_name}"
    conn = await asyncpg.connect(app_dsn)
    try:
        await conn.execute(packaged_schema_path().read_text(encoding="utf-8"))
        await ensure_migrations_table(conn)
        for path in _numbered_migrations():
            number = int(path.name.split("_", 1)[0])
            if number > _PRE_043:
                continue
            if path.name in EXTENSION_GATED_MIGRATIONS:
                # The issue's check names 037..043 as the pending set, which
                # is what a host WITH the extensions sees. Record the gated
                # pair as applied without executing, so hosts without AGE/
                # pgml (this machine) replay the same pending set.
                await conn.execute(
                    "INSERT INTO corpus.schema_migrations (filename) VALUES ($1) "
                    "ON CONFLICT DO NOTHING",
                    path.name,
                )
                continue
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO corpus.schema_migrations (filename) VALUES ($1) "
                "ON CONFLICT DO NOTHING",
                path.name,
            )
    finally:
        await conn.close()
    return app_dsn


@pytest.mark.requires_postgres
async def test_pre043_scratch_applies_037_to_044_once() -> None:
    """The issue Check: 037..044 apply, a second run applies nothing."""
    from tests.research_db import DB_NAME, drop_research_db

    app_dsn = await _bootstrap_pre_043(DB_NAME)
    try:
        first = await run_migrations(app_dsn)
        assert first.backend == "postgres"
        assert first.pending == [
            "037_ingest_queue.sql",
            "038_ingest_queue_progress.sql",
            "040_entities_doc_link_no_fk.sql",
            "041_inplace_update_indexes.sql",
            "043_project_tables_registry.sql",
            "044_ingest_ledger_doc_repair.sql",
        ]
        assert first.applied_now == first.pending
        second = await run_migrations(app_dsn)
        assert second.pending == []
        assert second.applied_now == []
    finally:
        await drop_research_db()


@pytest.mark.requires_postgres
async def test_superuser_run_hands_ownership_to_app_role() -> None:
    """With --superuser-dsn the app role ends up owning every table."""
    import asyncpg

    from tests.research_db import DB_NAME, drop_research_db, super_dsn_for

    app_dsn = await _bootstrap_pre_043(DB_NAME)
    try:
        report = await run_migrations(app_dsn, superuser_dsn=super_dsn_for(DB_NAME))
        assert report.applied_now == report.pending
        conn = await asyncpg.connect(app_dsn)
        try:
            rows = await conn.fetch(
                "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname IN ('public', 'corpus')"
            )
            owners = {row["tableowner"] for row in rows}
            assert owners == {"corpus_user"}
        finally:
            await conn.close()
    finally:
        await drop_research_db()


@pytest.mark.requires_postgres
async def test_session_settings_take_effect() -> None:
    """work_mem reaches the migration session (0.6.0 needed 1GB)."""
    import asyncpg

    from corpus_kb._setup.migrate_admin import apply_session_settings
    from tests.research_db import DSN, create_research_db, drop_research_db

    await create_research_db()
    try:
        conn = await asyncpg.connect(DSN)
        try:
            await apply_session_settings(conn, {"work_mem": "64MB"})
            assert await conn.fetchval("SHOW work_mem") == "64MB"
        finally:
            await conn.close()
        with pytest.raises(ConfigError):
            await run_migrations(DSN, session_settings={"work_mem": "nope drop"})
    finally:
        await drop_research_db()
