"""Tests for the SQL migration runner."""

from __future__ import annotations

import asyncpg
import pytest

from scripts.migrate import run_migrations


@pytest.mark.asyncio
async def test_migration_idempotency() -> None:
    """Running migrations twice on the same database is a no-op."""
    # Disposable test database only. NEVER point migration tests at the primary
    # corpus_kb database: a re-run can attempt ownership-only DDL and, more
    # dangerously, sibling tests TRUNCATE these tables.
    conn_str = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test"
    try:
        conn = await asyncpg.connect(conn_str)
        await conn.close()
    except Exception:
        pytest.skip("Postgres not available")
    await run_migrations(conn_str)
    await run_migrations(conn_str)
