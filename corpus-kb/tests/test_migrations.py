"""Tests for the SQL migration runner."""

from __future__ import annotations

import pytest

from scripts.migrate import run_migrations


@pytest.mark.asyncio
async def test_migration_idempotency() -> None:
    """Running migrations twice on the same database is a no-op."""
    conn_str = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test"
    await run_migrations(conn_str)
    await run_migrations(conn_str)
