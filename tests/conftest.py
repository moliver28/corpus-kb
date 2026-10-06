"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import functools
import importlib
import os
import socket
import sys
from urllib.parse import urlparse

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
    config.addinivalue_line(
        "markers",
        "requires_krippendorff: mark test as needing the krippendorff package",
    )


def _postgres_dsn() -> str:
    return os.environ.get(
        "CORPUS_KB_DATABASE_URL",
        "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test",
    )


@functools.lru_cache(maxsize=1)
def _postgres_reachable() -> bool:
    """TCP-probe the configured Postgres host:port (once per session)."""
    parsed = urlparse(_postgres_dsn())
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip tests that require capabilities unavailable on the current host."""
    if "requires_hi_res" in item.keywords:
        if sys.platform == "win32":
            pytest.skip("hi_res requires detectron2, unavailable on native Windows")
        try:
            importlib.import_module("detectron2")
        except ImportError:
            pytest.skip("hi_res requires detectron2")
    if "requires_postgres" in item.keywords and not _postgres_reachable():
        pytest.skip("Postgres not reachable")


@pytest.fixture
async def pg_pool():
    """Provide an asyncpg connection pool for tests.

    Skips tests if Postgres is not available.
    """
    dsn = os.environ.get(
        "CORPUS_KB_DATABASE_URL",
        "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test",
    )
    try:
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=2,
            timeout=5,
        )
    except Exception:
        pytest.skip("Postgres not available")
    yield pool
    await pool.close()


@pytest.fixture
async def graph_store(pg_pool):
    """Provide a PostgresGraphStore for tests."""
    from corpus_kb.storage.graph_store import PostgresGraphStore

    store = PostgresGraphStore(pg_pool)
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Research-domain throwaway database (todo-11): requested-only session
# fixture — tests that never ask for it pay nothing; CI (no Postgres) skips
# via the requires_postgres marker before the fixture runs.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def research_dsn():
    import asyncio
    import os

    import research_db

    dsn = asyncio.run(research_db.create_research_db())
    saved = os.environ.get("CORPUS_KB_DATABASE_URL")
    saved_snapshot = os.environ.get("CORPUS_KB_SNAPSHOT_PERIOD")
    research_db.set_env(dsn)
    asyncio.run(research_db.reset_singletons())

    async def _bind_app() -> None:
        from corpus_kb.domain.application import get_app

        get_app()  # construct the singleton while env points at the throwaway DB

    asyncio.run(_bind_app())
    # Restore immediately: the eventsourcing singleton stays bound to the
    # throwaway DB, but pg_pool-based tests keep their original DSN.
    if saved is None:
        os.environ.pop("CORPUS_KB_DATABASE_URL", None)
    else:
        os.environ["CORPUS_KB_DATABASE_URL"] = saved
    if saved_snapshot is None:
        os.environ.pop("CORPUS_KB_SNAPSHOT_PERIOD", None)
    else:
        os.environ["CORPUS_KB_SNAPSHOT_PERIOD"] = saved_snapshot
    yield dsn
    asyncio.run(research_db.drop_research_db())


@pytest.fixture
async def research_pool(research_dsn):
    import asyncpg

    pool = await asyncpg.create_pool(research_dsn, min_size=1, max_size=4)
    yield pool
    await pool.close()


@pytest.fixture
async def superuser(research_dsn):
    import research_db

    conn = await research_db.superuser_conn()
    yield conn
    await conn.close()


@pytest.fixture
async def reader_and_app(research_dsn):
    from corpus_kb.domain.application import get_app
    from corpus_kb.projections.event_reader import EventReader

    app = get_app()
    pool = await __import__("asyncpg").create_pool(research_dsn)
    try:
        yield EventReader(pool, app.mapper, app.recorder.events_table_name), app
    finally:
        await pool.close()


@pytest.fixture
async def pool(research_dsn):
    import asyncpg

    pool = await asyncpg.create_pool(research_dsn, min_size=1, max_size=4)
    yield pool
    await pool.close()
