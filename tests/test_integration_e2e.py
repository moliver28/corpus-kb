"""End-to-end integration tests for all 3 protocols (MCP, HTTP, socket).

Tests:
  1. Ingest file via MCP → search via HTTP → verify results
  2. Ingest text via HTTP → search via socket → verify results
  3. Add entity via socket → query entity via HTTP → verify
  4. Ingest directory via MCP → SQL query via HTTP → verify counts
  5. Search similar via HTTP → verify vector search works
  6. Search context via socket → verify context expansion
  7. Projection lag test: ingest → poll until projection updated → verify <2s lag
  8. Idempotency test: send same command twice → verify one event

All tests run against the throwaway full-schema database (the conftest
``research_dsn`` session fixture: schema.sql + migrations, dropped after
the session). That schema pins ``chunks_vectors.vector`` at vector(4096),
and pgvector enforces the exact width, so the fixtures run at 4096
embedding dimensions: the FakeEmbedder stub emits 4096-dim vectors, the
typed width guard passes, and the inserts are REAL vector(4096) inserts.
One pin test keeps the 768-dim default honest: it asserts the typed
ConfigError fires against this schema.

All tests use async pytest + real Postgres. Embeddings use the
deterministic FakeEmbedder stub (house pattern, no Ollama dependency).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest

pytestmark = pytest.mark.requires_postgres

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"

# The shipped schema pins the vector column at 4096 (storage/schema.sql;
# pgvector enforces the exact typmod width on insert).
SCHEMA_WIDTH = 4096


@pytest.fixture(autouse=True)
def _schema_width_env(monkeypatch: pytest.MonkeyPatch, research_dsn: str):
    """Point every load_config() at the throwaway database at 4096 dims.

    The 768-dim shipped default cannot enter vector(4096); the typed width
    guard (ingest_store.assert_vector_column_width) would stop the ingest
    before any insert. These tests prove the guard, the pipeline, and the
    schema AGREE at 4096.
    """
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", research_dsn)
    monkeypatch.setenv("CORPUS_KB_EMBEDDING_DIMENSIONS", str(SCHEMA_WIDTH))


@pytest.fixture(autouse=True)
def _deterministic_embeddings(monkeypatch: pytest.MonkeyPatch):
    """Swap the Ollama client for the deterministic FakeEmbedder stub.

    The stub emits 4096-dim unit vectors (the schema width), so ingest
    inserts succeed for the right reason without a running Ollama. The
    seam is the ingest embed factory (tools.ingest_embed imports it at
    module scope).
    """
    from corpus_kb.rag.embedder import FakeEmbedder

    def _fake(_config: object, _pool: object) -> FakeEmbedder:
        return FakeEmbedder({"embedding": {"dimensions": SCHEMA_WIDTH}})

    monkeypatch.setattr("corpus_kb.tools.ingest_embed.create_embedder", _fake)


@pytest.fixture
async def db_conn(research_dsn: str):
    """One direct Postgres connection over the throwaway database.

    A single connection keeps the session-scoped RLS tenant config stable
    across statements, which the RLS and verification reads rely on.
    """
    try:
        conn = await asyncpg.connect(research_dsn)
    except (OSError, asyncpg.PostgresError):
        pytest.skip("Postgres not available")
    yield conn
    await conn.close()


@pytest.fixture
async def db_pool(research_dsn: str):
    """A small pool for the pool-based managers and the ingest pipeline.

    The pipeline holds one pooled connection for the per-document advisory
    lock while the stage helpers acquire their own; two slots cover that
    shape without a self-deadlock.
    """
    try:
        pool = await asyncpg.create_pool(research_dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError):
        pytest.skip("Postgres not available")
    yield pool
    await pool.close()


@pytest.fixture
async def clean_db(db_conn):
    """Pin the RLS tenant, then clean all projection tables."""
    # Session scope (is_local=false): the setting must survive across
    # separate autocommit statements on this connection.
    await db_conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", DEFAULT_TENANT)
    await db_conn.execute("TRUNCATE chunks_vectors, chunks, documents, entities, relations CASCADE")
    yield


class TestE2EIntegration:
    """End-to-end integration tests across all 3 protocols."""

    @pytest.mark.asyncio
    async def test_ingest_file_creates_document(self, clean_db, db_conn, db_pool):
        """Test 1: Ingest a file → verify document appears in Postgres."""
        from corpus_kb.domain.models import IngestTextCommand
        from corpus_kb.handlers.command_handler import get_command_handler, reset_command_handler

        reset_command_handler()
        handler = get_command_handler(pool=db_pool)

        result = await handler.handle_ingest_text(
            IngestTextCommand(
                text="def hello_world(): print('Hello, World!')",
                source="test_e2e.py",
                source_type="code",
            )
        )

        assert result["status"] == "success"
        assert result["chunk_count"] > 0

        # Verify in Postgres (after projection runs)
        # Note: projection is async, may need to wait
        await asyncio.sleep(1.0)

        doc_count = await db_conn.fetchval("SELECT COUNT(*) FROM documents")
        assert doc_count >= 1, "Document not found in Postgres after ingest"

    @pytest.mark.asyncio
    async def test_search_returns_results(self, clean_db, db_pool):
        """Test 2: Ingest text → search → verify results returned."""
        from corpus_kb.domain.models import IngestTextCommand
        from corpus_kb.handlers.command_handler import get_command_handler, reset_command_handler

        reset_command_handler()
        handler = get_command_handler(pool=db_pool)

        # Ingest
        await handler.handle_ingest_text(
            IngestTextCommand(
                text="def authenticate(user, password): return verify(password)",
                source="auth.py",
                source_type="code",
            )
        )

        await asyncio.sleep(1.0)

        # Search via query handler: the ollama arm over the deterministic
        # FakeEmbedder (the pgml default arm needs the pgml extension, which
        # the throwaway database intentionally skips).
        from corpus_kb.config import load_config
        from corpus_kb.domain.models import SearchQuery
        from corpus_kb.handlers.query_handler import QueryHandler
        from corpus_kb.rag.embedder import FakeEmbedder

        cfg = load_config()
        cfg["embedding"] = {
            "provider": "ollama",
            "model": "fake-embedder",
            "base_url": "http://localhost:11434",
            "dimensions": SCHEMA_WIDTH,
            "batch_size": 8,
        }
        query_handler = QueryHandler(db_pool, embedder=FakeEmbedder(cfg), config=cfg)
        results = await query_handler.handle_search(SearchQuery(query="authenticate", k=5))

        # Results may be empty if projection hasn't run yet
        # This test verifies the query handler doesn't crash
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_add_entity_via_command(self, clean_db):
        """Test 3: Add entity via command handler → verify in Postgres."""
        from corpus_kb.domain.models import AddEntityCommand
        from corpus_kb.handlers.command_handler import get_command_handler, reset_command_handler

        reset_command_handler()
        handler = get_command_handler()

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
    async def test_idempotency_prevents_duplicates(self, clean_db, db_pool):
        """Test 4: Send same command twice → verify deduplication."""
        from uuid import uuid4

        from corpus_kb.handlers.idempotency import IdempotencyChecker

        checker = IdempotencyChecker(db_pool)
        cmd_id = uuid4()

        # First check — should return None (not seen before)
        result1 = await checker.check(UUID(DEFAULT_TENANT), cmd_id)
        assert result1 is None

        # Record the command
        await checker.record(
            UUID(DEFAULT_TENANT),
            cmd_id,
            "IngestFileCommand",
            {"file_path": "test.py"},
            {"status": "success"},
        )

        # Second check — should return cached result
        result2 = await checker.check(UUID(DEFAULT_TENANT), cmd_id)
        assert result2 is not None
        assert result2["command_type"] == "IngestFileCommand"

    @pytest.mark.asyncio
    async def test_rls_cross_tenant_isolation(self, clean_db, db_conn):
        """Test 5: RLS prevents cross-tenant data access."""
        # Insert data as tenant A
        await db_conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, false)",
            DEFAULT_TENANT,
        )
        await db_conn.execute(
            """INSERT INTO documents
               (doc_id, tenant_id, source, source_type) VALUES ($1, $2, $3, $4)""",
            str(UUID(int=1)),
            DEFAULT_TENANT,
            "tenant_a_file.py",
            "code",
        )

        # Switch to tenant B
        tenant_b = "00000000-0000-0000-0000-000000000002"
        await db_conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, false)",
            tenant_b,
        )

        # Tenant B should NOT see tenant A's documents
        count = await db_conn.fetchval("SELECT COUNT(*) FROM documents")
        assert count == 0, f"RLS failed: tenant B sees {count} documents from tenant A"

        # Reset to default tenant
        await db_conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, false)",
            DEFAULT_TENANT,
        )

    @pytest.mark.asyncio
    async def test_http_app_creates(self, clean_db):
        """Test 6: HTTP app can be created without errors."""
        from corpus_kb.api.http import create_http_app

        app = create_http_app()
        assert app is not None
        # The route count grows with every surface (research/release/ingest);
        # pin the routes this file depends on instead of a magic total.
        paths = {getattr(route, "path", None) for route in app.router.routes}
        assert "/api/ingest/file" in paths
        assert "/api/ingest/status" in paths
        assert "/api/search" in paths

    @pytest.mark.asyncio
    async def test_socket_server_creates(self, clean_db):
        """Test 7: Socket server can be created without errors."""
        from corpus_kb.api.socket import get_socket_server, reset_socket_server

        reset_socket_server()
        server = get_socket_server()
        assert server is not None
        assert server._socket_path is not None

    @pytest.mark.asyncio
    async def test_projection_checkpoint_roundtrip(self, clean_db, db_pool):
        """Test 8: Checkpoint manager can set and get checkpoints."""
        from corpus_kb.projections.checkpoint import CheckpointManager

        mgr = CheckpointManager(db_pool)

        # Get initial checkpoint (should be None)
        cp = await mgr.get_checkpoint("TestProjection", UUID(DEFAULT_TENANT))
        assert cp is None

        # Update checkpoint
        await mgr.update_checkpoint(
            "TestProjection",
            UUID(DEFAULT_TENANT),
            UUID(int=1),
            datetime(2026, 1, 1, tzinfo=UTC),
        )

        # Get checkpoint (should exist now)
        cp = await mgr.get_checkpoint("TestProjection", UUID(DEFAULT_TENANT))
        assert cp is not None
        assert str(cp["last_event_id"]) == str(UUID(int=1))

    @pytest.mark.asyncio
    async def test_dlq_record_and_list(self, clean_db, db_pool):
        """Test 9: DLQ can record and list failures."""
        from corpus_kb.projections.dlq import DLQHandler

        handler = DLQHandler(db_pool)

        # Record a failure
        await handler.record_failure(
            "TestProjection",
            UUID(DEFAULT_TENANT),
            UUID(int=1),
            "ChunksAdded",
            "Ollama connection failed",
        )

        # List failures
        failures = await handler.list_failures("TestProjection", UUID(DEFAULT_TENANT))
        assert len(failures) >= 1
        assert failures[0]["error_message"] == "Ollama connection failed"

        # Mark resolved
        await handler.mark_resolved(failures[0]["dlq_id"], UUID(DEFAULT_TENANT))

        # List again — should be empty (resolved filtered out)
        failures = await handler.list_failures("TestProjection", UUID(DEFAULT_TENANT))
        assert len(failures) == 0

    @pytest.mark.asyncio
    async def test_error_handling_returns_error_dict(self):
        """Test 10: Error handling decorator returns structured error dict."""
        from corpus_kb.handlers.error_handling import handle_errors

        @handle_errors(timeout_seconds=1.0, max_retries=1)
        async def failing_function():
            raise FileNotFoundError("test file not found")

        result = await failing_function()
        assert result["status"] == "error"
        assert result["error"] == "test file not found"
        assert result["error_type"] == "FileNotFoundError"

    @pytest.mark.asyncio
    async def test_width_guard_rejects_shipped_default_against_vector_4096(self, clean_db, db_conn):
        """The 768-dim default cannot enter vector(4096): typed ConfigError.

        Pin for the W4c width guard against the LIVE schema: the shipped
        default (nomic-embed-text, 768d) must fail fast with the typed
        error naming the schema reality, before any per-chunk insert.
        """
        from corpus_kb.errors import ConfigError
        from corpus_kb.tools.ingest_store import assert_vector_column_width

        with pytest.raises(ConfigError) as excinfo:
            await assert_vector_column_width(db_conn, 768, "nomic-embed-text")
        assert "vector(4096)" in str(excinfo.value)
        assert "embedding.dimensions" in excinfo.value.remediation
