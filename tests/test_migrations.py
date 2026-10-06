"""Tests for the SQL migration runner."""

from __future__ import annotations

import os
import re
from pathlib import Path

import asyncpg
import pytest

from corpus_kb._setup.migrate import run_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "src" / "corpus_kb" / "migrations"
AGE_MIGRATION = MIGRATIONS_DIR / "004_enable_age.sql"
PGML_MIGRATION = MIGRATIONS_DIR / "005_enable_pgml.sql"

CODING_MIGRATIONS = [
    MIGRATIONS_DIR / "012_coding_schema.sql",
    MIGRATIONS_DIR / "013_force_rls_coding.sql",
    MIGRATIONS_DIR / "014_codebook_versions_unique.sql",
    MIGRATIONS_DIR / "015_chunk_signals_upsert.sql",
]
CODING_ROLLBACKS = [
    MIGRATIONS_DIR / "rollback_012_coding_schema.sql",
    MIGRATIONS_DIR / "rollback_013_force_rls_coding.sql",
    MIGRATIONS_DIR / "rollback_014_codebook_versions_unique.sql",
    MIGRATIONS_DIR / "rollback_015_chunk_signals_upsert.sql",
]
CODING_TABLES = [
    "code_registry",
    "codebook_versions",
    "code_keywords",
    "chunk_signals",
    "chunk_keyword_hits",
    "answer_ctx_vectors",
    "batch_status",
    "chunk_codes",
    "final_codes",
    "chunk_status",
    "doc_extraction_audit",
    "coding_gate_decisions",
]
CODING_MIGRATION_NAMES = [p.name for p in CODING_MIGRATIONS]
PROBE_ROLE = "corpus_tenant_probe"


def _dsn() -> str:
    """Container-first DSN: CI and local probes point at 5433 via env var;
    the bare-metal default remains master's 5432."""
    return os.environ.get(
        "CORPUS_KB_DATABASE_URL",
        "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb",
    )


async def _connect_or_skip() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(_dsn())
    except Exception:
        pytest.skip("Postgres not available")


async def _apply_coding_migrations(conn: asyncpg.Connection) -> None:
    """Ensure 012-015 are applied, tolerating extension-gated base migrations.

    004/005 need the AGE/pgml server packages; on hosts without them the runner
    aborts before reaching the coding migrations. If the coding schema is
    already present (the documented migration-rehearsal path: ops applies
    012-015 against a prepared database), the live assertions still run;
    otherwise the test skips with the runner's failure as the reason.
    """
    try:
        await run_migrations(_dsn())
        return
    except (asyncpg.FeatureNotSupportedError, asyncpg.UndefinedFileError) as exc:
        present = await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname = ANY($1::text[])",
            CODING_TABLES,
        )
        if present != len(CODING_TABLES):
            pytest.skip(f"server extension unavailable, coding schema absent: {exc}")


@pytest.mark.asyncio
async def test_migration_idempotency() -> None:
    """Running migrations twice on the same database is a no-op."""
    conn_str = _dsn()
    try:
        conn = await asyncpg.connect(conn_str)
        await conn.close()
    except Exception:
        pytest.skip("Postgres not available")
    try:
        await run_migrations(conn_str)
        await run_migrations(conn_str)
    except (asyncpg.FeatureNotSupportedError, asyncpg.UndefinedFileError) as exc:
        # 004/005 require AGE/pgml server packages; absent here => service-gated.
        # Any other error class (syntax, duplicate graph) must fail the test.
        pytest.skip(f"server extension unavailable: {exc}")


def test_age_pgml_migrations_exist_and_match_runner_convention() -> None:
    """004/005 exist and are discovered by the migration runner's filename rule."""
    for path in (AGE_MIGRATION, PGML_MIGRATION):
        assert path.is_file(), f"missing migration: {path.name}"
        assert re.match(r"^\d+_", path.name), f"runner ignores {path.name}"
    names = sorted(p.name for p in MIGRATIONS_DIR.iterdir() if p.suffix == ".sql")
    assert names.index("004_enable_age.sql") < names.index("005_enable_pgml.sql")


def test_age_pgml_migrations_are_idempotent_sql() -> None:
    """Re-applying 004/005 must never error: IF NOT EXISTS + guarded create_graph."""
    age_sql = AGE_MIGRATION.read_text(encoding="utf-8").lower()
    pgml_sql = PGML_MIGRATION.read_text(encoding="utf-8").lower()
    assert "create extension if not exists age" in age_sql
    assert "create extension if not exists pgml" in pgml_sql
    # create_graph raises when the graph exists; it must be guarded by a
    # NOT EXISTS probe against the ag_catalog.ag_graph catalog.
    assert "create_graph" in age_sql
    assert "ag_catalog.ag_graph" in age_sql
    assert "not exists" in age_sql


@pytest.mark.asyncio
async def test_age_pgml_migrations_apply_twice_on_live_db() -> None:
    """Executing each new migration's SQL twice directly succeeds (no tracking table)."""
    conn = await _connect_or_skip()
    try:
        for path in (AGE_MIGRATION, PGML_MIGRATION):
            sql = path.read_text(encoding="utf-8")
            try:
                await conn.execute(sql)
            except (asyncpg.FeatureNotSupportedError, asyncpg.UndefinedFileError) as exc:
                pytest.skip(f"extension not available on this server: {exc}")
            # Second apply must be a no-op success, not an error.
            await conn.execute(sql)
    finally:
        await conn.close()


def test_coding_migrations_exist_and_match_runner_convention() -> None:
    """012-015 exist in runner order; their rollbacks exist but are runner-exempt."""
    for path in CODING_MIGRATIONS:
        assert path.is_file(), f"missing migration: {path.name}"
        assert re.match(r"^\d+_", path.name), f"runner ignores {path.name}"
    names = sorted(p.name for p in MIGRATIONS_DIR.iterdir() if p.suffix == ".sql")
    order = [names.index(name) for name in CODING_MIGRATION_NAMES]
    assert order == sorted(order), f"coding migrations out of order: {CODING_MIGRATION_NAMES}"
    for path in CODING_ROLLBACKS:
        assert path.is_file(), f"missing rollback: {path.name}"
        # The runner only auto-applies ^\d+_ files; rollbacks must stay manual.
        assert not re.match(r"^\d+_", path.name), f"runner would auto-apply {path.name}"


def test_coding_migrations_are_idempotent_sql() -> None:
    """Re-applying 012-015 must never error at the SQL level."""
    schema_sql = CODING_MIGRATIONS[0].read_text(encoding="utf-8").lower()
    # 012 re-creates objects: every CREATE INDEX/TABLE must be IF NOT EXISTS,
    # policies must be guarded by an exception handler.
    assert "create table if not exists code_registry" in schema_sql
    assert "create index if not exists" in schema_sql
    assert "exception when others" in schema_sql
    # 014 adds a constraint only when absent; 015 drops-then-recreates its key.
    unique_sql = CODING_MIGRATIONS[2].read_text(encoding="utf-8").lower()
    assert "if not exists" in unique_sql
    upsert_sql = CODING_MIGRATIONS[3].read_text(encoding="utf-8").lower()
    assert "drop constraint if exists chunk_signals_pkey" in upsert_sql
    assert "drop trigger if exists chunk_signals_append_only_trigger" in upsert_sql


@pytest.mark.asyncio
async def test_coding_migrations_apply_twice_on_live_db() -> None:
    """Executing each coding migration's SQL directly a second time succeeds."""
    conn = await _connect_or_skip()
    try:
        await _apply_coding_migrations(conn)
        for path in CODING_MIGRATIONS:
            sql = path.read_text(encoding="utf-8")
            await conn.execute(sql)
            # Second apply must be a no-op success, not an error.
            await conn.execute(sql)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_coding_rls_forced_and_tenant_scoped_on_live_db() -> None:
    """All coding tables are FORCE RLS and opaque to a non-owner tenant role."""
    conn = await _connect_or_skip()
    try:
        await _apply_coding_migrations(conn)
        rows = await conn.fetch(
            """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relname = ANY($1::text[])
            """,
            CODING_TABLES,
        )
        assert len(rows) == len(CODING_TABLES), "some coding tables are missing"
        for row in rows:
            assert row["relrowsecurity"] is True, f"{row['relname']} not RLS-enabled"
            assert row["relforcerowsecurity"] is True, f"{row['relname']} not FORCE RLS"

        # Probe: a non-owner role sees zero rows of another tenant's data.
        grants = " ".join(f"GRANT SELECT ON {table} TO {PROBE_ROLE};" for table in CODING_TABLES)
        await conn.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{PROBE_ROLE}') THEN
                    CREATE ROLE {PROBE_ROLE} NOLOGIN;
                END IF;
                BEGIN
                    EXECUTE 'GRANT {PROBE_ROLE} TO ' || quote_ident(current_user);
                EXCEPTION WHEN OTHERS THEN
                    RAISE WARNING 'could not grant membership: %', SQLERRM;
                END;
            END $$;
            {grants}
            """
        )
        try:
            await conn.execute(f"SET ROLE {PROBE_ROLE}")
        except asyncpg.InsufficientPrivilegeError:
            pytest.skip("cannot SET ROLE to the non-owner probe role")
        try:
            await conn.execute("SET app.current_tenant_id = '00000000-0000-0000-0000-000000000009'")
            count = await conn.fetchval("SELECT count(*) FROM code_registry")
            assert count == 0, "non-owner role saw rows belonging to another tenant"
        finally:
            await conn.execute("RESET ROLE")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_coding_migrations_rollback_and_reapply_on_live_db() -> None:
    """Rollback 015->012 drops the coding domain; re-running migrations restores it."""
    conn = await _connect_or_skip()
    try:
        await _apply_coding_migrations(conn)

        for path in reversed(CODING_ROLLBACKS):
            await conn.execute(path.read_text(encoding="utf-8"))
        leftovers = await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname = ANY($1::text[])",
            CODING_TABLES,
        )
        assert leftovers == 0, "coding tables survived the rollback chain"

        # The runner still lists 012-015 as applied; a manual rollback must
        # clear those rows or the re-apply would be skipped as a no-op.
        await conn.execute(
            "DELETE FROM corpus.schema_migrations WHERE filename = ANY($1::text[])",
            CODING_MIGRATION_NAMES,
        )
        try:
            await run_migrations(_dsn())
        except (asyncpg.FeatureNotSupportedError, asyncpg.UndefinedFileError):
            # Service-gated hosts (no AGE/pgml) re-apply the coding domain
            # via the direct migration-rehearsal path.
            for path in CODING_MIGRATIONS:
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(
                    "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)"
                    " ON CONFLICT DO NOTHING",
                    path.name,
                )

        forced = await conn.fetch(
            """
            SELECT relname
            FROM pg_class
            WHERE relname = ANY($1::text[])
              AND relrowsecurity AND relforcerowsecurity
            """,
            CODING_TABLES,
        )
        assert len(forced) == len(CODING_TABLES), "re-applied coding tables lack FORCE RLS"

        # Re-apply idempotency holds after a full rollback/re-apply cycle.
        await conn.execute(CODING_MIGRATIONS[0].read_text(encoding="utf-8"))
    finally:
        await conn.close()
