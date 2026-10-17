"""The shared schema bootstrap, on a scratch live database.

Pins the ONE ``apply_schema`` implementation that ``corpus-kb
quickstart`` and ``tests/research_db.py`` share: schema.sql verbatim,
numbered migrations tracked in ``corpus.schema_migrations``, the
extension-gated 004/005 skipped, and a re-run touching nothing.

Requires Postgres (``requires_postgres``; auto-skipped in CI without
one). The scratch bootstrap mirrors tests/research_db.py exactly:
extensions installed as superuser, migrations applied as corpus_user.
"""

from __future__ import annotations

import asyncpg
import pytest

from corpus_kb._setup.schema_apply import EXTENSION_GATED_MIGRATIONS, apply_schema
from tests.research_db import SUPER_DSN, create_research_db, drop_research_db

SCRATCH_DB = "corpus_kb_schema_apply_test"
SCRATCH_DSN = f"postgresql://corpus_user:corpus_pass@localhost:5432/{SCRATCH_DB}"


async def _create_scratch() -> None:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{SCRATCH_DB}" OWNER corpus_user')
    await admin.close()
    # Extensions first, exactly like tests/research_db.py: the scratch role
    # is not a superuser, so it cannot CREATE EXTENSION itself.
    su = await asyncpg.connect(SCRATCH_DSN, user="postgres", password="postgres")
    await su.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await su.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    await su.close()


async def _drop_scratch() -> None:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
    await admin.close()


@pytest.mark.requires_postgres
async def test_apply_schema_tracks_migrations_and_is_idempotent() -> None:
    """One run applies everything ungated; a second run applies nothing."""
    await _create_scratch()
    try:
        result = await apply_schema(SCRATCH_DSN)
        assert result.applied, "a fresh database must apply migrations"
        assert "001_corpus_schema.sql" in result.applied
        assert set(result.skipped) == EXTENSION_GATED_MIGRATIONS
        assert not (set(result.applied) & EXTENSION_GATED_MIGRATIONS)

        conn = await asyncpg.connect(SCRATCH_DSN)
        try:
            rows = await conn.fetch("SELECT filename FROM corpus.schema_migrations")
            recorded = {row["filename"] for row in rows}
            assert set(result.applied) <= recorded
            assert not (recorded & EXTENSION_GATED_MIGRATIONS)
            tables = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            names = {row["tablename"] for row in tables}
            assert {"chunks", "documents"} <= names
        finally:
            await conn.close()

        again = await apply_schema(SCRATCH_DSN)
        assert again.applied == []
        assert set(again.skipped) == EXTENSION_GATED_MIGRATIONS
    finally:
        await _drop_scratch()


@pytest.mark.requires_postgres
async def test_research_db_harness_uses_the_shared_bootstrap() -> None:
    """The refactored research_db still provisions a full scratch stack."""
    dsn = await create_research_db()
    try:
        conn = await asyncpg.connect(dsn)
        try:
            count = await conn.fetchval("SELECT count(*) FROM corpus.schema_migrations")
            assert count is not None and count > 20
            tables = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            names = {row["tablename"] for row in tables}
            assert "chunks" in names
        finally:
            await conn.close()
    finally:
        await drop_research_db()
