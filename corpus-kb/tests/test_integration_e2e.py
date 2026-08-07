"""End-to-end integration tests across the command, query, projection, and API layers.

All DB-backed tests use a real asyncpg pool against the disposable corpus_kb_test
database (never the primary corpus_kb) and real Postgres. Tests that only construct
objects (HTTP app, socket server, error decorator) need no database.

Requires corpus_kb_test to exist with migrations 001-005 applied and corpus_user
owning its tables. See docs/INSTALL.md for setup.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

# On Windows the socket transport uses a named pipe; skip the whole module if it
# is unavailable there. On POSIX this predicate is always False (never skips).
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" and not Path(r"\\.\pipe\corpus-kb").exists(),
    reason="Socket transport unavailable on this Windows host",
)

DB_URL = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test"
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
CONFIG = {"graph": {"extract_entities": True, "backend": "postgres"}}


@pytest.fixture
async def pg_pool():
    """asyncpg pool against the disposable test database. Skips if Postgres is down."""
    try:
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4, timeout=5)
    except Exception:
        pytest.skip("Postgres not available")
    yield pool
    await pool.close()


@pytest.fixture
async def db_conn(pg_pool):
    """A single connection drawn from the pool, for tests that drive raw SQL."""
    async with pg_pool.acquire() as conn:
        yield conn


@pytest.fixture
async def clean_db(pg_pool):
    """Truncate every projection/data table so each test starts from empty."""
    from src.storage.tenant_conn import tenant_connection

    async with tenant_connection(pg_pool, DEFAULT_TENANT) as conn:
        await conn.execute(
            "TRUNCATE chunks_vectors, chunks, documents, entities, relations, "
            "projection_checkpoints, projection_dlq, idempotency_keys, "
            "tags, document_tags, metadata CASCADE"
        )
    yield


class TestE2EIntegration:
    """End-to-end integration tests across the command/query/projection/API layers."""

    @pytest.mark.asyncio
    async def test_ingest_file_creates_document(self, clean_db, pg_pool):
        """Ingest text via the command handler -> document is persisted in Postgres."""
        from src.handlers.command_handler import (
            get_command_handler,
            reset_command_handler,
        )
        from src.domain.application import get_app, reset_app
        from src.domain.models import IngestTextCommand
        from src.storage.tenant_conn import tenant_connection

        reset_app()
        reset_command_handler()
        get_app(DB_URL)  # prime the eventsourcing app against the test database
        handler = get_command_handler(CONFIG, pg_pool)

        result = await handler.handle_ingest_text(
            IngestTextCommand(
                text="def hello_world(): print('Hello, World!')",
                source="test_e2e.py",
                source_type="code",
            )
        )
        assert result["status"] == "success"
        assert result["chunk_count"] > 0

        async with tenant_connection(pg_pool, DEFAULT_TENANT) as conn:
            doc_count = await conn.fetchval("SELECT COUNT(*) FROM documents")
        assert doc_count >= 1, "Document not found in Postgres after ingest"

    @pytest.mark.asyncio
    async def test_search_returns_results(self, clean_db, pg_pool):
        """Ingest then search -> query handler returns a list without crashing."""
        from src.handlers.command_handler import (
            get_command_handler,
            reset_command_handler,
        )
        from src.domain.application import get_app, reset_app
        from src.domain.models import IngestTextCommand, SearchQuery
        from src.handlers.query_handler import QueryHandler

        reset_app()
        reset_command_handler()
        get_app(DB_URL)
        handler = get_command_handler(CONFIG, pg_pool)
        await handler.handle_ingest_text(
            IngestTextCommand(
                text="def authenticate(user, password): return verify(password)",
                source="auth.py",
                source_type="code",
            )
        )

        query_handler = QueryHandler(pg_pool)
        results = await query_handler.handle_search(SearchQuery(query="authenticate", k=5))
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_add_entity_via_command(self, clean_db, pg_pool):
        """Add an entity via the command handler -> success dict with an entity_id."""
        from src.handlers.command_handler import (
            get_command_handler,
            reset_command_handler,
        )
        from src.domain.application import get_app, reset_app
        from src.domain.models import AddEntityCommand

        reset_app()
        reset_command_handler()
        get_app(DB_URL)
        handler = get_command_handler(CONFIG, pg_pool)

        result = handler.handle_add_entity(
            AddEntityCommand(
                name="UserService",
                entity_type="class",
                metadata={"file": "user_service.py"},
            )
        )
        assert result["status"] == "success"
        assert "entity_id" in result

    @pytest.mark.asyncio
    async def test_idempotency_prevents_duplicates(self, clean_db, pg_pool):
        """Recording a command id makes the second check return the cached result."""
        from src.handlers.idempotency import IdempotencyChecker

        checker = IdempotencyChecker(pg_pool)
        cmd_id = uuid4()

        result1 = await checker.check(UUID(DEFAULT_TENANT), cmd_id)
        assert result1 is None

        await checker.record(
            UUID(DEFAULT_TENANT),
            cmd_id,
            "IngestFileCommand",
            {"file_path": "test.py"},
            {"status": "success"},
        )

        result2 = await checker.check(UUID(DEFAULT_TENANT), cmd_id)
        assert result2 is not None
        assert result2["command_type"] == "IngestFileCommand"

    @pytest.mark.asyncio
    async def test_rls_cross_tenant_isolation(self, clean_db, db_conn):
        """RLS hides tenant A's rows from tenant B on the same connection."""
        # set_config(..., true) is transaction-scoped, so every set_config and the
        # query that relies on it must share ONE explicit transaction. Within it,
        # the latest set_config applies to subsequent statements.
        async with db_conn.transaction():
            await db_conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", DEFAULT_TENANT
            )
            await db_conn.execute(
                "INSERT INTO documents (doc_id, tenant_id, source, source_type) "
                "VALUES ($1, $2, $3, $4)",
                str(UUID(int=1)),
                DEFAULT_TENANT,
                "tenant_a_file.py",
                "code",
            )

            tenant_b = "00000000-0000-0000-0000-000000000002"
            await db_conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", tenant_b
            )
            count = await db_conn.fetchval("SELECT COUNT(*) FROM documents")
            assert count == 0, f"RLS failed: tenant B sees {count} documents from tenant A"

    @pytest.mark.asyncio
    async def test_http_app_creates(self):
        """The HTTP app builds and registers its key API routes."""
        from src.api.http import create_http_app

        app = create_http_app()
        assert app is not None
        paths = {getattr(r, "path", None) for r in app.router.routes}
        for expected in ("/api/ingest/text", "/api/search", "/api/query/sql"):
            assert expected in paths, f"missing route {expected}"

    @pytest.mark.asyncio
    async def test_socket_server_creates(self):
        """The JSON-RPC socket server constructs with a socket path."""
        from src.api.socket import get_socket_server, reset_socket_server

        reset_socket_server()
        server = get_socket_server()
        assert server is not None
        assert server._socket_path is not None

    @pytest.mark.asyncio
    async def test_projection_checkpoint_roundtrip(self, clean_db, pg_pool):
        """CheckpointManager stores and reads back a projection checkpoint."""
        from src.projections.checkpoint import CheckpointManager

        mgr = CheckpointManager(pg_pool)

        cp = await mgr.get_checkpoint("TestProjection", UUID(DEFAULT_TENANT))
        assert cp is None

        await mgr.update_checkpoint(
            "TestProjection", UUID(DEFAULT_TENANT), UUID(int=1), "2026-01-01T00:00:00Z"
        )

        cp = await mgr.get_checkpoint("TestProjection", UUID(DEFAULT_TENANT))
        assert cp is not None
        assert str(cp["last_event_id"]) == str(UUID(int=1))

    @pytest.mark.asyncio
    async def test_dlq_record_and_list(self, clean_db, pg_pool):
        """DLQHandler records a failure, lists it, and hides it once resolved."""
        from src.projections.dlq import DLQHandler

        handler = DLQHandler(pg_pool)

        await handler.record_failure(
            "TestProjection",
            UUID(DEFAULT_TENANT),
            UUID(int=1),
            "ChunksAdded",
            "Ollama connection failed",
        )

        failures = await handler.list_failures("TestProjection", UUID(DEFAULT_TENANT))
        assert len(failures) >= 1
        assert failures[0]["error_message"] == "Ollama connection failed"

        await handler.mark_resolved(failures[0]["dlq_id"], UUID(DEFAULT_TENANT))

        failures = await handler.list_failures("TestProjection", UUID(DEFAULT_TENANT))
        assert len(failures) == 0

    @pytest.mark.asyncio
    async def test_error_handling_returns_error_dict(self):
        """The handle_errors decorator turns a raised exception into an error dict."""
        from src.handlers.error_handling import handle_errors

        @handle_errors(timeout_seconds=1.0, max_retries=1)
        async def failing_function():
            raise FileNotFoundError("test file not found")

        result = await failing_function()
        assert result["status"] == "error"
        assert result["error"] == "test file not found"
        assert result["error_type"] == "FileNotFoundError"
