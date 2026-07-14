"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import importlib
import sys

import asyncpg
import pytest


def pytest_configure(config: object) -> None:
    config.addinivalue_line(
        "markers",
        "requires_ollama: mark test as needing a running Ollama service",
    )
    config.addinivalue_line(
        "markers",
        "requires_hi_res: mark test as needing Unstructured hi_res (detectron2)",
    )
    config.addinivalue_line(
        "markers",
        "requires_postgres: mark test as needing a running Postgres instance",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip tests that require capabilities unavailable on the current host."""
    if "requires_hi_res" in item.keywords:
        if sys.platform == "win32":
            pytest.skip("hi_res requires detectron2, unavailable on native Windows")
        try:
            importlib.import_module("detectron2")
        except ImportError:
            pytest.skip("hi_res requires detectron2")


@pytest.fixture
async def pg_pool():
    """Provide an asyncpg connection pool for tests."""
    pool = await asyncpg.create_pool(
        "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test",
        min_size=2,
        max_size=5,
    )
    yield pool
    await pool.close()


@pytest.fixture
async def graph_store(pg_pool):
    """Provide a PostgresGraphStore for tests."""
    from src.storage.graph_store import PostgresGraphStore

    store = PostgresGraphStore(pg_pool)
    yield store
    await store.close()
