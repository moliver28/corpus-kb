"""Migration 023 static checks + (PG-gated) apply/rollback/reapply behavior.

Static checks run offline: column/index statements, the config-gated FTS
lockstep (search.qa_context_in_fts, DEFAULT FALSE), the ts_rank/IDF
rationale comment, runner discovery, and rollback completeness. The
behavioral test mirrors the scratch-DB harness of test_shadow_migration.py
and skips when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import asyncpg
import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
MIGRATION_023 = MIGRATIONS / "023_qa_context.sql"
ROLLBACK_023 = MIGRATIONS / "rollback_023_qa_context.sql"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"

MIGRATION_TEXT = MIGRATION_023.read_text(encoding="utf-8")
ROLLBACK_TEXT = ROLLBACK_023.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Static checks (offline)
# ---------------------------------------------------------------------------


def test_migration_adds_qa_context_column() -> None:
    assert "ADD COLUMN IF NOT EXISTS qa_context JSONB" in MIGRATION_TEXT


def test_migration_creates_jsonb_gin_index() -> None:
    assert "idx_chunks_qa_context" in MIGRATION_TEXT
    assert "USING gin (qa_context)" in MIGRATION_TEXT


def test_migration_fts_lockstep_is_config_gated_default_off() -> None:
    assert "current_setting('search.qa_context_in_fts', true)" in MIGRATION_TEXT
    assert "idx_chunks_fts_qa_context" in MIGRATION_TEXT
    # the pinned rationale: ts_rank has no IDF, so default-off is soundness
    assert "ts_rank has no IDF" in MIGRATION_TEXT


def test_migration_uses_009_do_block_pattern() -> None:
    assert "EXCEPTION WHEN OTHERS THEN" in MIGRATION_TEXT
    assert "RAISE WARNING" in MIGRATION_TEXT


def test_migration_is_runner_discovered_and_rollback_is_not() -> None:
    runner_pattern = re.compile(r"^\d+_")
    assert runner_pattern.match(MIGRATION_023.name)
    assert not runner_pattern.match(ROLLBACK_023.name)


def test_rollback_drops_indexes_and_column() -> None:
    assert "DROP INDEX IF EXISTS idx_chunks_qa_context" in ROLLBACK_TEXT
    assert "DROP INDEX IF EXISTS idx_chunks_fts_qa_context" in ROLLBACK_TEXT
    assert "ALTER TABLE chunks DROP COLUMN IF EXISTS qa_context" in ROLLBACK_TEXT


# ---------------------------------------------------------------------------
# Behavioral test (scratch DB; skips without Postgres)
# ---------------------------------------------------------------------------

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
DB_NAME = "corpus_kb_m023_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
SUPER_DSN = f"{BASE_DSN}/postgres"


async def _create_db() -> str:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user')
    finally:
        await admin.close()
    # Pre-create the extensions schema.sql needs, as the superuser (same
    # harness step as tests/test_ingest_integrity.py).
    superconn = await asyncpg.connect(DSN, user="postgres", password="postgres")
    try:
        await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    finally:
        await superconn.close()
    conn = await asyncpg.connect(DSN)
    try:
        # Same >2000-dim index strip as tests/research_db.py (pgvector limit).
        base_sql = re.sub(
            r"CREATE INDEX[^;]*ON chunks_vectors USING (ivfflat|hnsw)\s*\([^;]*vector[^;]*;",
            "-- index on vector(4096) omitted: exceeds pgvector 2000-dim limit",
            SCHEMA_SQL.read_text(encoding="utf-8"),
        )
        await conn.execute(base_sql)
    finally:
        await conn.close()
    return DSN


async def _drop_db() -> None:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture(scope="module")
def m023_dsn() -> str:
    try:
        return asyncio.run(_create_db())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres unavailable for the migration 023 scratch DB: {exc}")


@pytest.mark.requires_postgres
async def test_023_apply_twice_idempotent_then_opt_in_fts_then_rollback(m023_dsn: str) -> None:
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(MIGRATION_TEXT)
        await conn.execute(MIGRATION_TEXT)  # run-twice idempotent

        column = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='chunks' AND column_name='qa_context'"
        )
        assert column == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_chunks_qa_context'"
            )
            == 1
        )
        # DEFAULT FALSE: the FTS expression index must NOT exist.
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_chunks_fts_qa_context'"
            )
            == 0
        )

        # Opt-in at the session level (production: ALTER ROLE/DATABASE ... SET),
        # then re-run — the 009-style DO block creates the FTS index.
        await conn.execute("SELECT set_config('search.qa_context_in_fts', 'on', false)")
        await conn.execute(MIGRATION_TEXT)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_chunks_fts_qa_context'"
            )
            == 1
        )
        await conn.execute("SELECT set_config('search.qa_context_in_fts', 'off', false)")

        # Rollback drops column + both indexes; re-apply restores them.
        await conn.execute(ROLLBACK_TEXT)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name='chunks' AND column_name='qa_context'"
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname IN "
                "('idx_chunks_qa_context', 'idx_chunks_fts_qa_context')"
            )
            == 0
        )
        await conn.execute(MIGRATION_TEXT)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name='chunks' AND column_name='qa_context'"
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_chunks_qa_context'"
            )
            == 1
        )
    finally:
        await conn.close()
