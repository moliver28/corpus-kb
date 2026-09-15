"""Verify required Postgres extensions are installed in the test database."""

from __future__ import annotations

import pytest

REQUIRED_EXTENSIONS = {"vector", "age", "pgml"}


@pytest.mark.requires_postgres
async def test_required_postgres_extensions(pg_pool):
    """Assert that vector, age, and pgml extensions are present.

    The ``pg_pool`` fixture skips this test gracefully when Postgres is
    unavailable, so the test suite can still pass without a local database.
    """
    rows = await pg_pool.fetch("SELECT extname FROM pg_extension;")
    installed = {row["extname"] for row in rows}
    missing = REQUIRED_EXTENSIONS - installed
    assert not missing, f"Missing required Postgres extensions: {missing}"
