"""Tests for the SQL migration runner."""

from __future__ import annotations

import re
from pathlib import Path

import asyncpg
import pytest

from scripts.migrate import run_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
AGE_MIGRATION = MIGRATIONS_DIR / "004_enable_age.sql"
PGML_MIGRATION = MIGRATIONS_DIR / "005_enable_pgml.sql"


@pytest.mark.asyncio
async def test_migration_idempotency() -> None:
    """Running migrations twice on the same database is a no-op."""
    conn_str = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
    try:
        conn = await asyncpg.connect(conn_str)
        await conn.close()
    except Exception:
        pytest.skip("Postgres not available")
    try:
        await run_migrations(conn_str)
        await run_migrations(conn_str)
    except asyncpg.FeatureNotSupportedError as exc:
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
    conn_str = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
    try:
        conn = await asyncpg.connect(conn_str)
    except Exception:
        pytest.skip("Postgres not available")
    try:
        for path in (AGE_MIGRATION, PGML_MIGRATION):
            sql = path.read_text(encoding="utf-8")
            try:
                await conn.execute(sql)
            except asyncpg.FeatureNotSupportedError as exc:
                pytest.skip(f"extension not available on this server: {exc}")
            # Second apply must be a no-op success, not an error.
            await conn.execute(sql)
    finally:
        await conn.close()
