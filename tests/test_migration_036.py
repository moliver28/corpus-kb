"""Migration 036 live verification: the ingest ledger's project link (#185).

Runs against a THROWAWAY database (drop/create per run): applies
storage/schema.sql + migrations 001+ (skipping the extension-gated 004/005
pair and 036 itself, per the test_migration_044 precedent), seeds one
pre-036 ledger row, then drives the migration:

* the project_id column lands as a plain nullable uuid;
* the (tenant_id, project_id) index lands beside it;
* a pre-migration ledger row keeps project_id NULL (no backfill);
* the step is idempotent (a second application changes nothing);
* the rollback drops the index and the column, keeps the row, and a
  re-apply restores both with the link still NULL.

Credentials follow the storage portability program rule 20: admin and app
DSNs come from tests/research_db.py (POSTGRES_* env). Auto-skips when no
Postgres admin connection resolves, never failing on the environment.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
import research_db

pytestmark = pytest.mark.requires_postgres

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"
MIGRATION_036 = MIGRATIONS / "036_ingest_runs_project.sql"
ROLLBACK_036 = MIGRATIONS / "rollback_036_ingest_runs_project.sql"

TENANT = "00000000-0000-0000-0000-000000000001"
RUN_ID = "55555555-5555-5555-5555-555555555555"

# Env-derived seams (tests/research_db.py); see test_migration_044.py.
DB_NAME = f"corpus_kb_mig036_{os.getpid() % 100000}_test"
DSN = f"{research_db.BASE_DSN}/{DB_NAME}"

# DDL identifiers cannot be bound. Guard the constant first, then key the
# statements, like tests/research_db.py does.
if not re.fullmatch(r"[a-z_0-9]+", DB_NAME):
    raise ValueError(f"DB_NAME must be a bare identifier, got {DB_NAME!r}")

_EXTENSION_GATED = {"004_enable_age.sql", "005_enable_pgml.sql"}
_SKIP_AT_BOOTSTRAP = _EXTENSION_GATED | {"036_ingest_runs_project.sql"}

_PROJECT_INDEX = "idx_ingest_runs_tenant_project"


async def _drop_db() -> None:
    """Best-effort drop through the admin DSN; never masks a skip."""
    admin = await asyncpg.connect(research_db.super_dsn_for("postgres"))
    try:
        statements = {"drop": f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)'}
        await admin.execute(statements["drop"])
    finally:
        await admin.close()


async def _bootstrap() -> None:
    await _drop_db()
    admin = await asyncpg.connect(research_db.super_dsn_for("postgres"))
    try:
        statements = {"create": f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user'}
        await admin.execute(statements["create"])
    finally:
        await admin.close()

    superconn = await asyncpg.connect(research_db.super_dsn_for(DB_NAME))
    try:
        await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    finally:
        await superconn.close()

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        from corpus_kb._setup.migrate import ensure_migrations_table

        await ensure_migrations_table(conn)
        for path in sorted(MIGRATIONS.iterdir()):
            if path.suffix != ".sql" or not path.name[0].isdigit():
                continue
            if path.name in _SKIP_AT_BOOTSTRAP:
                continue
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)"
                " ON CONFLICT DO NOTHING",
                path.name,
            )
    finally:
        await conn.close()


async def _project_state(conn: asyncpg.Connection) -> tuple[object, object]:
    """(column present, index present) for the ledger's project link."""
    column = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'ingest_runs' "
        "AND column_name = 'project_id'"
    )
    index = await conn.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE tablename = 'ingest_runs' AND indexname = $1",
        _PROJECT_INDEX,
    )
    return (column, index)


async def test_project_column_is_nullable_indexed_and_never_backfilled() -> None:
    try:
        await _bootstrap()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres unavailable for the migration 036 scratch DB: {exc}")

    conn = await asyncpg.connect(DSN)
    superconn = await asyncpg.connect(research_db.super_dsn_for(DB_NAME))
    try:
        # Seed the pre-036 state: a ledger row with no project link.
        # ingest_runs is FORCE RLS (022), so the seed sets the tenant GUC.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", TENANT)
            await conn.execute(
                "INSERT INTO ingest_runs (run_id, tenant_id, source_type)"
                " VALUES ($1, $2, 'transcript')",
                UUID(RUN_ID),
                UUID(TENANT),
            )

        migration = MIGRATION_036.read_text(encoding="utf-8")
        await conn.execute(migration)

        row = await conn.fetchrow(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'ingest_runs' "
            "AND column_name = 'project_id'"
        )
        assert row is not None, "migration 036 must add ingest_runs.project_id"
        assert row["data_type"] == "uuid", "the link is a uuid, like documents.project_id"
        assert row["is_nullable"] == "YES", "the link is a plain nullable column, no FK"

        # Row reads go through BYPASSRLS (test_migration_044 shape): FORCE RLS
        # makes a GUC-less read return zero rows, never an error, so an app
        # read could confuse "hidden" with "NULL".
        linked = await superconn.fetchval(
            "SELECT project_id FROM ingest_runs WHERE run_id = $1", UUID(RUN_ID)
        )
        assert linked is None, "pre-036 ledger rows must stay NULL (no backfill)"

        index_def = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'ingest_runs' AND indexname = $1",
            _PROJECT_INDEX,
        )
        assert index_def is not None, "the (tenant_id, project_id) index must land"
        assert "tenant_id" in index_def and "project_id" in index_def

        # Idempotent: a second application changes nothing.
        await conn.execute(migration)
        column_count, index_count = await _project_state(conn)
        assert (column_count, index_count) == (1, 1)
        assert (
            await superconn.fetchval(
                "SELECT project_id FROM ingest_runs WHERE run_id = $1", UUID(RUN_ID)
            )
            is None
        )

        # The rollback drops the index and the column, and keeps the row.
        await conn.execute(ROLLBACK_036.read_text(encoding="utf-8"))
        column_count, index_count = await _project_state(conn)
        assert (column_count, index_count) == (0, 0)
        kept = await superconn.fetchval(
            "SELECT count(*) FROM ingest_runs WHERE run_id = $1", UUID(RUN_ID)
        )
        assert kept == 1, "the rollback must keep the ledger row itself"

        # A re-apply restores the link; the row reads NULL again.
        await conn.execute(migration)
        column_count, index_count = await _project_state(conn)
        assert (column_count, index_count) == (1, 1)
        assert (
            await superconn.fetchval(
                "SELECT project_id FROM ingest_runs WHERE run_id = $1", UUID(RUN_ID)
            )
            is None
        )
    finally:
        await conn.close()
        await superconn.close()
        with contextlib.suppress(OSError, asyncpg.PostgresError):
            await _drop_db()
