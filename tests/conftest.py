"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import functools
import importlib
import os
import socket
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest

# Pin ``tests`` to this directory BEFORE any test module imports.
#
# The repo's tests/ has no __init__.py on purpose (pytest collects modules
# root-free so siblings import each other by bare module name). A stray
# top-level ``tests`` package inside a PyPI wheel (textstat ships one) then
# shadows this directory and breaks ``from tests.mock_pg import ...`` in the
# DB test modules. Registering the package explicitly wins that race.
_TESTS_DIR = Path(__file__).resolve().parent


def _pin_local_tests_package() -> None:
    existing = sys.modules.get("tests")
    existing_paths = [Path(p).resolve() for p in getattr(existing, "__path__", ())]
    if _TESTS_DIR in existing_paths:
        return
    package = types.ModuleType("tests")
    package.__path__ = [str(_TESTS_DIR)]
    sys.modules["tests"] = package


_pin_local_tests_package()


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

        # Bind by explicit DSN: the app cache is keyed on (backend, DSN), so
        # a bare get_app() would resolve config.yaml (the compose stack) and
        # build a second app on the wrong database.
        get_app(dsn)

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
async def fresh_research_app(research_dsn: str) -> str:
    """Rebind the research eventsourcing app for ONE test, on its own loop.

    The session ``research_dsn`` fixture binds the app once, on the
    throwaway event loop of an ``asyncio.run`` call. Handing that stale
    app (whose psycopg pool is pinned to a dead loop) to later tests ends
    in ``PoolTimeout: couldn't get a connection after 30.00 sec`` some
    distance into the session — observed on the CI leg and in long local
    sessions alike. Each research test instead resets the singletons,
    points the env at the throwaway database (no-arg ``get_app()`` calls
    resolve it), and builds a fresh app on the current loop. The env is
    restored on teardown so later pg_pool-based tests keep their own DSN.
    """
    import os

    import research_db

    saved_url = os.environ.get("CORPUS_KB_DATABASE_URL")
    saved_snapshot = os.environ.get("CORPUS_KB_SNAPSHOT_PERIOD")
    research_db.set_env(research_dsn)
    await research_db.reset_singletons()
    from corpus_kb.domain.application import get_app

    get_app(research_dsn)
    yield research_dsn
    # Close THIS test's app (its pool is pinned to this test's loop) before
    # the loop goes away, then restore the env exactly like the session
    # fixture does.
    await research_db.reset_singletons()
    if saved_url is None:
        os.environ.pop("CORPUS_KB_DATABASE_URL", None)
    else:
        os.environ["CORPUS_KB_DATABASE_URL"] = saved_url
    if saved_snapshot is None:
        os.environ.pop("CORPUS_KB_SNAPSHOT_PERIOD", None)
    else:
        os.environ["CORPUS_KB_SNAPSHOT_PERIOD"] = saved_snapshot


@pytest.fixture
async def research_pool(fresh_research_app: str):
    import asyncpg

    pool = await asyncpg.create_pool(fresh_research_app, min_size=1, max_size=4)
    yield pool
    await pool.close()


@pytest.fixture
async def superuser(research_dsn):
    import research_db

    conn = await research_db.superuser_conn()
    yield conn
    await conn.close()


@pytest.fixture
async def reader_and_app(fresh_research_app: str):
    from corpus_kb.domain.application import get_app
    from corpus_kb.projections.event_reader import EventReader

    dsn = fresh_research_app
    app = get_app(dsn)
    pool = await __import__("asyncpg").create_pool(dsn)
    try:
        yield EventReader(pool, app.mapper, app.recorder.events_table_name), app
    finally:
        await pool.close()


@pytest.fixture
async def pool(fresh_research_app: str):
    import asyncpg

    pool = await asyncpg.create_pool(fresh_research_app, min_size=1, max_size=4)
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# DuckDB gateway fixtures (storage portability program, rules 21 and 23).
# The backend-parametrized fixture takes its file PATH as a fixture argument,
# never the env var (research_dsn mutates CORPUS_KB_DATABASE_URL). Teardown
# order is rule 21: close apps, then gateways, then delete files — Windows
# raises PermissionError when an open database file is removed, so fixtures
# always close().
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_path(tmp_path: Path) -> Path:
    """A fresh DuckDB database file path for one test."""
    return tmp_path / "gateway.duckdb"


@pytest.fixture
async def duckdb_gateway(duckdb_path: Path):
    from corpus_kb.storage.gateway import DuckDbGateway

    gateway = await DuckDbGateway.connect(str(duckdb_path))
    try:
        yield gateway
    finally:
        await gateway.close()


@pytest.fixture(scope="session")
def duckdb_session_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-long DuckDB file path (rule 23: passed as a fixture argument)."""
    return tmp_path_factory.mktemp("duckdb-session") / "session.duckdb"


@pytest.fixture(scope="session")
async def duckdb_session_gateway(duckdb_session_path: Path):
    from corpus_kb.storage.gateway import DuckDbGateway

    gateway = await DuckDbGateway.connect(str(duckdb_session_path))
    try:
        yield gateway
    finally:
        await gateway.close()
        # Delete only after close; duckdb writes a `<file>.wal` sidecar.
        for suffix in ("", ".wal"):
            Path(f"{duckdb_session_path}{suffix}").unlink(missing_ok=True)


#: Storage backends the suite can exercise today. The postgres leg joins when
#: the full-parity harness lands (the PG service is live on the ubuntu CI leg
#: since phase B); without Postgres the pg_pool fixture skips that leg.
STORAGE_BACKENDS = ("duckdb",)


@pytest.fixture(params=STORAGE_BACKENDS)
def backend(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
async def any_gateway(backend: str, request: pytest.FixtureRequest, duckdb_path: Path):
    """Backend-parametrized SqlGateway: a DuckDB file gateway or the PG pool."""
    from corpus_kb.storage.gateway import DuckDbGateway, PostgresGateway

    if backend == "duckdb":
        gateway = await DuckDbGateway.connect(str(duckdb_path))
        try:
            yield gateway
        finally:
            await gateway.close()
        return
    pool = await request.getfixturevalue("pg_pool")
    yield PostgresGateway(pool)
