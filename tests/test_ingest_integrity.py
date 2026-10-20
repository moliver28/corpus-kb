"""Ingest integrity: ingest_runs ledger, degraded flags, advisory lock, store split.

Offline tests pin the migration contracts and run_pipeline's decision logic
against a stub store. The Postgres-marked tests use the m021-style throwaway
database (schema.sql + the numeric migration chain). Following the documented
harness pattern in tests/test_shadow_migration.py, the global FORCE-RLS
migrations (011/013) are skipped here, and migration 022's transient backfill
window re-FORCEs chunks/chunks_vectors, so the harness lifts FORCE again after
the chain: it keeps testing the pre-011 conditions it was built against.
(The v1 store methods' bare-autocommit GUC defect that originally forced this
avoidance was fixed in #87 — the tenant GUC is now set inside
``conn.transaction()``; re-enabling FORCE against this harness is a separate
live-PG verification.)
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any, ClassVar

import asyncpg
import pytest

from corpus_kb.errors import ConfigError
from tests.research_db import RLS_PROBE_ROLE, ensure_rls_probe, super_dsn_for

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
MIGRATION_022 = MIGRATIONS / "022_ingest_integrity.sql"
ROLLBACK_022 = MIGRATIONS / "rollback_022_ingest_integrity.sql"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
DB_NAME = "corpus_kb_ir022_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
# Admin/extension connections follow the env-derived research_db contract
# (program rule 20): locally the superuser is postgres, in the CI service
# image it is the bootstrap role corpus_user. No hardcoded role overrides.
SUPER_DSN = super_dsn_for("postgres")

TENANT = "00000000-0000-0000-0000-000000000002"

# Same skip list as tests/test_shadow_migration.py (rationale in its comment):
# extension-gated 004/005 plus the two global FORCE-RLS migrations 011/013.
_EXTENSION_GATED = {
    "004_enable_age.sql",
    "005_enable_pgml.sql",
    "011_force_rls.sql",
    "013_force_rls_coding.sql",
}


# ---------------------------------------------------------------------------
# Static migration contract pins (offline)
# ---------------------------------------------------------------------------


def test_migration_022_pins_ledger_shape() -> None:
    text = MIGRATION_022.read_text(encoding="utf-8").lower()
    assert "create table if not exists ingest_runs" in text
    for column in ("run_id", "tenant_id", "start_hash", "finish_hash", "stages jsonb", "error"):
        assert column in text, f"ingest_runs column contract lost: {column}"
    assert "check (status in ('running','completed','failed'))" in text
    # Dedupe probe + doctor/watch indexes.
    assert "idx_ingest_runs_tenant_start_hash" in text
    assert "where status = 'running'" in text


def test_migration_022_pins_degraded_flags_and_partial_index() -> None:
    text = MIGRATION_022.read_text(encoding="utf-8").lower()
    assert "alter table chunks add column if not exists degraded" in text
    assert "alter table chunks_vectors add column if not exists degraded" in text
    assert re.search(
        r"idx_chunks_vectors_degraded\s*on chunks_vectors\(tenant_id\) where degraded", text
    )


def test_migration_022_is_idempotent_and_rls_forced() -> None:
    text = MIGRATION_022.read_text(encoding="utf-8").lower()
    # House RLS convention (016/021): ENABLE + DROP POLICY IF EXISTS + GUC
    # policy + FORCE, warn-and-continue per table.
    assert "drop policy if exists" in text
    assert "force row level security" in text
    assert "exception when others" in text
    # Transient NO FORCE RLS window for the tenant-blind backfill.
    assert "no force row level security" in text
    assert "vector_dims" in text  # per-row zero-vector construction


def test_migration_022_backfill_targets_zero_vectors_idempotently() -> None:
    text = MIGRATION_022.read_text(encoding="utf-8")
    assert "WHERE degraded = FALSE" in text, "backfill must be a no-op on re-run"
    assert "l2_distance" in text


def test_rollback_022_drops_everything_migration_022_created() -> None:
    text = ROLLBACK_022.read_text(encoding="utf-8")
    assert "DROP INDEX IF EXISTS idx_chunks_vectors_degraded" in text
    assert "ALTER TABLE chunks_vectors DROP COLUMN IF EXISTS degraded" in text
    assert "ALTER TABLE chunks DROP COLUMN IF EXISTS degraded" in text
    assert "DROP TABLE IF EXISTS ingest_runs" in text


def test_migration_022_backfill_window_re_raises_rls_failures() -> None:
    """The transient NO FORCE/FORCE blocks re-raise, never warn-and-proceed.

    The runner wraps the file in one transaction: a swallowed NO FORCE
    failure would record a partial backfill as applied while RLS stayed
    forced — every non-GUC tenant's zero-vector poison rows would stay
    unflagged and invisible to repair/search honesty.
    """
    text = MIGRATION_022.read_text(encoding="utf-8")
    assert text.count("RAISE;") == 4  # NO FORCE x2 + FORCE x2
    assert "RAISE WARNING 'Could not" not in text


def test_schema_sql_documents_migration_022_columns() -> None:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    assert text.count("migration 022") >= 2, (
        "schema.sql must document the migration-022 degraded columns for both "
        "chunks and chunks_vectors (reference-DDL comment convention)"
    )


# ---------------------------------------------------------------------------
# Store-split contract (offline)
# ---------------------------------------------------------------------------


def test_ingest_store_split_keeps_reexport_identity() -> None:
    import corpus_kb.tools.ingest_common as ingest_module
    import corpus_kb.tools.ingest_store as store_module

    assert ingest_module.PostgresIngestStore is store_module.PostgresIngestStore
    assert ingest_module.DEFAULT_TENANT_ID == store_module.DEFAULT_TENANT_ID
    # Orchestration stays in ingest_common; storage + ledger in ingest_store.
    for name in ("run_pipeline", "embed_chunks", "extract_with_fallback", "ingest_text"):
        assert hasattr(ingest_module, name)
    for name in (
        "unchanged_doc_id",
        "begin_run",
        "finish_run",
        "fail_run",
        "update_document",
        "store_chunks",
        "store_vectors",
        "store_entities",
        "store_relations",
    ):
        assert hasattr(store_module.PostgresIngestStore, name)


def test_advisory_lock_contract_is_pinned_in_orchestrator() -> None:
    import inspect

    import corpus_kb.tools.ingest_common as ingest_module

    source = inspect.getsource(ingest_module)
    assert "pg_try_advisory_lock" in source
    assert "hashtextextended" in source
    assert "concurrent-run" in source


# ---------------------------------------------------------------------------
# Embedding failure classification (offline unit)
# ---------------------------------------------------------------------------


def _chunk(chunk_id: str, embedding: list[float] | None) -> Any:
    from corpus_kb.utils.models import Chunk

    chunk = Chunk(chunk_id=chunk_id, document_id="d", text="x", source_type="text")
    chunk.embedding = embedding
    return chunk


def test_zero_vector_embedding_is_fatal() -> None:
    from corpus_kb.tools.ingest_common import _embedding_failure_is_fatal

    assert _embedding_failure_is_fatal([_chunk("a", [0.0, 0.0, 0.0])]) is True
    assert _embedding_failure_is_fatal([]) is True
    assert _embedding_failure_is_fatal([_chunk("a", None)]) is True


def test_real_vector_embedding_is_not_fatal() -> None:
    from corpus_kb.tools.ingest_common import _embedding_failure_is_fatal

    assert _embedding_failure_is_fatal([_chunk("a", [0.5, 0.1])]) is False


# ---------------------------------------------------------------------------
# run_pipeline decision logic (offline, stub store + stub pool)
# ---------------------------------------------------------------------------


class _LockConn:
    """Fetchval answers the advisory-lock probe; execute counts unlocks."""

    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired
        self.executes: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> bool:
        return self._acquired

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append(sql)
        return "OK"


class _FakePool:
    def __init__(self, conn: _LockConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _LockConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _StubStore:
    """Records the orchestrator's calls; canned behavior via CLASS attributes
    (run_pipeline constructs its own store instance, so per-test canning must
    survive that construction)."""

    last: _StubStore | None = None

    unchanged_doc: str | None = None
    inserted_ids: ClassVar[set[str]] = set()
    raise_on_chunks: bool = False

    def __init__(self, pool: Any, tenant_id: str = "t") -> None:
        self.calls: list[str] = []
        self.finish_args: tuple[str, str, dict[str, object], list[str]] | None = None
        self.fail_args: tuple[str, str] | None = None
        _StubStore.last = self

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        self.calls.append("probe")
        return type(self).unchanged_doc

    async def begin_run(self, document: Any, file_hash: str) -> tuple[str, str]:
        self.calls.append("begin")
        return "doc-1", "run-1"

    async def store_chunks(self, chunks: list[Any]) -> set[str]:
        self.calls.append("chunks")
        if type(self).raise_on_chunks:
            raise RuntimeError("chunks boom")
        return set(type(self).inserted_ids)

    async def store_vectors(
        self,
        chunks: list[Any],
        embedding_model: str,
        dimensions: int = 768,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        self.calls.append("vectors")
        return len(chunks)

    async def store_entities(self, entities: list[Any]) -> int:
        self.calls.append("entities")
        return 0

    async def store_relations(
        self, relations: list[Any], model_version: Any = None, prompt_version: Any = None
    ) -> int:
        self.calls.append("relations")
        return 0

    async def update_document(self, document: Any, file_hash: str) -> None:
        self.calls.append("update")

    async def finish_run(
        self, run_id: str, file_hash: str, stages: dict[str, object], errors: list[str]
    ) -> None:
        self.calls.append("finish")
        self.finish_args = (run_id, file_hash, dict(stages), list(errors))

    async def fail_run(self, run_id: str, error: str) -> None:
        self.calls.append("fail")
        self.fail_args = (run_id, error)


class _AllInsertedStore(_StubStore):
    """Stub store whose store_chunks reports every chunk as newly inserted,
    so the orchestrator's embed stage receives real (non-empty) new_chunks."""

    async def store_chunks(self, chunks: list[Any]) -> set[str]:
        self.calls.append("chunks")
        return {str(c.chunk_id) for c in chunks}


def _ingest_config() -> dict[str, object]:
    # Hierarchy shadow write disabled: the stub pool has no shadow-path
    # methods and this file pins ledger/status decisions, not shadow rows.
    return {
        "graph": {"extract_entities": False},
        "embedding": {"provider": "pgml"},
        "ingest": {"chunking": {"enabled": False}},
    }


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    pool: _FakePool,
    config: dict[str, object],
    store_cls: type[_StubStore] = _StubStore,
    strategy_name: str | None = None,
) -> dict[str, object]:
    import corpus_kb.tools.ingest_common as ingest_module

    monkeypatch.setattr(ingest_module, "PostgresIngestStore", store_cls)
    return await ingest_module.run_pipeline(
        text="hello world",
        source_type="text",
        path="test-src",
        config=config,
        pg_pool=pool,  # type: ignore[arg-type]
        strategy_name=strategy_name,
    )


async def test_run_pipeline_lock_contention_skips_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _FakePool(_LockConn(acquired=False))
    _StubStore.last = None
    result = await _run(monkeypatch, pool, _ingest_config())

    assert result["status"] == "skipped"
    assert result["reason"] == "concurrent-run"
    assert result["errors"] == []
    # The store layer is never consulted: no run was begun, nothing skipped
    # silently.
    assert _StubStore.last is None


async def test_run_pipeline_unchanged_requires_completed_clean_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _FakePool(_LockConn(acquired=True))
    _StubStore.last = None
    # The probe (not the orchestrator) decides: only a completed clean run
    # yields a doc_id.
    _StubStore.unchanged_doc = "doc-9"
    try:
        result = await _run(monkeypatch, pool, _ingest_config())
    finally:
        _StubStore.unchanged_doc = None

    assert result["status"] == "skipped"
    assert result["reason"] == "unchanged"
    assert result["document_id"] == "doc-9"
    assert _StubStore.last is not None and "begin" not in _StubStore.last.calls


async def test_run_pipeline_clean_run_completes_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _FakePool(_LockConn(acquired=True))
    result = await _run(monkeypatch, pool, _ingest_config())

    assert result["status"] == "success"
    assert result["run_id"] == "run-1"
    stub = _StubStore.last
    assert stub is not None
    assert stub.calls[0] == "probe" and stub.calls[1] == "begin"
    assert stub.finish_args is not None
    run_id, _file_hash, stages, errors = stub.finish_args
    assert run_id == "run-1"
    assert errors == []  # completed ONLY when the stage error list is empty
    assert stages["errors"] == []
    assert "chunks" in stages and "vectors" in stages


async def test_run_pipeline_stage_error_is_partial_never_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _FakePool(_LockConn(acquired=True))
    _StubStore.raise_on_chunks = True
    try:
        result = await _run(monkeypatch, pool, _ingest_config())
    finally:
        _StubStore.raise_on_chunks = False

    assert result["status"] == "partial"
    assert any("PostgresWriteError" in e for e in result["errors"])
    stub_last = _StubStore.last
    assert stub_last is not None and stub_last.finish_args is not None
    assert stub_last.finish_args[3] != []  # ledger closed failed
    assert any("chunks boom" in e for e in stub_last.finish_args[3])


async def test_run_pipeline_zero_vectors_fail_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.tools.ingest_common as ingest_module

    pool = _FakePool(_LockConn(acquired=True))

    async def _zero_embed(
        chunks: list[Any],
        config: Any,
        pool: Any = None,
        text_override: Any = None,
        document_text: Any = None,
        meta: Any = None,
    ):
        for c in chunks:
            c.embedding = [0.0, 0.0]
        return True, "Connection failed: pgml and fallback ollama both returned zero vectors"

    monkeypatch.setattr(ingest_module, "embed_chunks", _zero_embed)
    result = await _run(monkeypatch, pool, _ingest_config(), store_cls=_AllInsertedStore)

    assert result["status"] == "partial"
    assert result["degraded"] is True
    assert any("EmbeddingError" in e for e in result["errors"])
    stub_last = _StubStore.last
    assert stub_last is not None and stub_last.finish_args is not None
    assert stub_last.finish_args[3] != []


async def test_run_pipeline_fallback_rescue_is_a_note_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.tools.ingest_common as ingest_module

    pool = _FakePool(_LockConn(acquired=True))

    async def _fallback_embed(
        chunks: list[Any],
        config: Any,
        pool: Any = None,
        text_override: Any = None,
        document_text: Any = None,
        meta: Any = None,
    ):
        for c in chunks:
            c.embedding = [0.7, 0.7]
        return True, "Connection failed: pgml returned zero vectors; fallback ollama produced"

    monkeypatch.setattr(ingest_module, "embed_chunks", _fallback_embed)
    result = await _run(monkeypatch, pool, _ingest_config(), store_cls=_AllInsertedStore)

    # Fallback produced real vectors: run completes (degraded), the note is
    # surfaced, and the dedupe probe can fire again afterwards.
    assert result["status"] == "success"
    assert result["degraded"] is True
    assert result["errors"] == []
    assert "embed_note" in result
    stub_last = _StubStore.last
    assert stub_last is not None and stub_last.finish_args is not None
    assert stub_last.finish_args[3] == []


async def test_run_pipeline_crash_marks_run_failed_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_kb.rag.contextualizer as contextualizer_module

    pool = _FakePool(_LockConn(acquired=True))

    class _Boom:
        def __init__(self, config: dict[str, object]) -> None:
            pass

        def generate_blurbs(self, text: str, chunk_texts: list[str]) -> list[str]:
            raise RuntimeError("contextual service down")

    monkeypatch.setattr(contextualizer_module, "ContextGenerator", _Boom)
    config: dict[str, object] = {
        "graph": {"extract_entities": False},
        "ingest": {"contextualize": {"enabled": True}, "chunking": {"enabled": False}},
    }

    with pytest.raises(RuntimeError, match="contextual service down"):
        await _run(monkeypatch, pool, config)

    stub_last = _StubStore.last
    assert stub_last is not None and stub_last.fail_args is not None
    run_id, error = stub_last.fail_args
    assert run_id == "run-1"
    assert "RuntimeError" in error and "contextual service down" in error
    assert "finish" not in stub_last.calls  # never closed as completed


async def test_run_pipeline_cancellation_marks_run_failed_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M3: CancelledError (watch-mode Ctrl-C) is a BaseException in 3.11+ and
    must still close the ledger — the run never strands at 'running'."""
    pool = _FakePool(_LockConn(acquired=True))

    class _CancelStore(_StubStore):
        async def store_chunks(self, chunks: list[Any]) -> set[str]:
            self.calls.append("chunks")
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _run(monkeypatch, pool, _ingest_config(), store_cls=_CancelStore)

    stub_last = _StubStore.last
    assert stub_last is not None and stub_last.fail_args is not None
    run_id, error = stub_last.fail_args
    assert run_id == "run-1"
    assert "CancelledError" in error
    assert "finish" not in stub_last.calls  # never closed as completed


async def test_run_pipeline_empty_plan_closes_ledger_failed_and_reports_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M4: a zero-chunk plan never completes clean — the ledger closes failed
    (so the unchanged-probe can never enshrine it) and empty_reason surfaces."""
    import corpus_kb.ingestion.registry as reg
    from corpus_kb.ingestion.base import ChunkPlan, DetectResult, SourceProfile

    class _EmptyStrategy:
        name = "empty-plan-strategy"

        def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
            return DetectResult(confidence=0.0, reason="none")

        def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
            return ChunkPlan(strategy=self.name, chunks=[], empty_reason="no turns parsed")

    reg.register_strategy(_EmptyStrategy())
    try:
        pool = _FakePool(_LockConn(acquired=True))
        result = await _run(
            monkeypatch, pool, _ingest_config(), strategy_name="empty-plan-strategy"
        )
    finally:
        reg._REGISTRY.pop("empty-plan-strategy", None)

    assert result["status"] == "partial"
    assert result["errors"] == ["EmptyPlanError: no turns parsed"]
    assert result["empty_reason"] == "no turns parsed"
    stub_last = _StubStore.last
    assert stub_last is not None and stub_last.finish_args is not None
    # Ledger closed failed -> the dedupe short-circuit can never fire for it.
    assert stub_last.finish_args[3] != []
    assert any("EmptyPlanError" in e for e in stub_last.finish_args[3])


async def test_run_pipeline_nonconformant_plan_raises_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M7: bad chunk indices raise PlanConformanceError before lock/ledger."""
    import corpus_kb.ingestion.registry as reg
    from corpus_kb.ingestion.base import (
        ChunkPlan,
        DetectResult,
        PlanConformanceError,
        PlannedChunk,
        SourceProfile,
    )

    class _BadIndicesStrategy:
        name = "bad-indices-strategy"

        def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
            return DetectResult(confidence=0.0, reason="none")

        def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
            return ChunkPlan(
                strategy=self.name,
                chunks=[PlannedChunk(text="a", index=0), PlannedChunk(text="b", index=5)],
            )

    reg.register_strategy(_BadIndicesStrategy())
    try:
        lock_conn = _LockConn(acquired=True)
        pool = _FakePool(lock_conn)
        _StubStore.last = None
        with pytest.raises(PlanConformanceError, match="bad-indices-strategy"):
            await _run(monkeypatch, pool, _ingest_config(), strategy_name="bad-indices-strategy")
        # Raised before the advisory lock AND the ledger: nothing written.
        assert lock_conn.executes == []
        assert _StubStore.last is None
    finally:
        reg._REGISTRY.pop("bad-indices-strategy", None)


# ---------------------------------------------------------------------------
# Postgres-marked integration: ledger semantics, advisory lock, degraded flags
# ---------------------------------------------------------------------------


async def _apply_schema_and_migrations(conn: Any) -> None:
    # The halfvec(2560) schema carries only LEGAL indexes — verbatim apply.
    await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    from corpus_kb._setup.migrate import ensure_migrations_table

    await ensure_migrations_table(conn)
    for path in sorted(MIGRATIONS.iterdir()):
        if path.suffix != ".sql" or not path.name[0].isdigit():
            continue
        if path.name in _EXTENSION_GATED:
            continue
        await conn.execute(path.read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO corpus.schema_migrations (filename) VALUES ($1) ON CONFLICT DO NOTHING",
            path.name,
        )


@pytest.fixture(scope="module")
def integrity_dsn():
    async def _create() -> str:
        import asyncpg

        admin = await asyncpg.connect(SUPER_DSN)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user')
        finally:
            await admin.close()

        superconn = await asyncpg.connect(super_dsn_for(DB_NAME))
        try:
            await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
            # The FORCE-RLS negative assertions below run through the shared
            # non-superuser probe role (see tests/research_db.py): the app
            # role is the bootstrap SUPERUSER in the CI service image, and a
            # superuser bypasses row level security entirely.
            await ensure_rls_probe(superconn)
        finally:
            await superconn.close()

        conn = await asyncpg.connect(DSN)
        try:
            await _apply_schema_and_migrations(conn)
            # The v1 path under test predates FORCE RLS (see module docstring):
            # 022's backfill window re-FORCEs chunks/chunks_vectors, so lift it
            # again to restore the pre-011 conditions this harness targets.
            await conn.execute("ALTER TABLE chunks NO FORCE ROW LEVEL SECURITY")
            await conn.execute("ALTER TABLE chunks_vectors NO FORCE ROW LEVEL SECURITY")
            # The probe role writes the ledger row in the FORCE-RLS test.
            await conn.execute(f"GRANT INSERT ON ingest_runs TO {RLS_PROBE_ROLE}")
        finally:
            await conn.close()
        return DSN

    return asyncio.run(_create())


@pytest.fixture
async def integrity_pool(integrity_dsn: str):
    import asyncpg

    pool = await asyncpg.create_pool(integrity_dsn, min_size=1, max_size=4)
    yield pool
    await pool.close()


async def _tenant_execute(pool: Any, sql: str, *args: Any) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", TENANT)
        await conn.execute(sql, *args)


async def _tenant_fetchrow(pool: Any, sql: str, *args: Any) -> Any:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", TENANT)
        return await conn.fetchrow(sql, *args)


@pytest.mark.requires_postgres
async def test_migration_022_run_twice_is_idempotent(integrity_pool) -> None:
    async with integrity_pool.acquire() as conn:
        await conn.execute(MIGRATION_022.read_text(encoding="utf-8"))
        await conn.execute(MIGRATION_022.read_text(encoding="utf-8"))
        row = await conn.fetchrow("SELECT status FROM ingest_runs LIMIT 1")
        assert row is None  # table exists and is empty; second apply was a no-op
        # Re-applying 022 re-FORCEs chunks/chunks_vectors (its transient NO
        # FORCE backfill window always ends in FORCE). Restore the pre-011
        # conditions this harness targets — same rationale as the fixture.
        await conn.execute("ALTER TABLE chunks NO FORCE ROW LEVEL SECURITY")
        await conn.execute("ALTER TABLE chunks_vectors NO FORCE ROW LEVEL SECURITY")


@pytest.mark.requires_postgres
async def test_ingest_runs_table_is_force_rls(integrity_pool) -> None:
    import asyncpg

    async with integrity_pool.acquire() as conn:
        forced = await conn.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'ingest_runs'"
        )
        assert forced is True
        # Owner writes without the tenant GUC are rejected (FORCE + policy).
        # Exercise it through the non-superuser probe role: the app role is
        # the bootstrap SUPERUSER in the CI service image, and a superuser
        # bypasses row level security entirely.
        await conn.execute(f"SET ROLE {RLS_PROBE_ROLE}")
        try:
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(
                    "INSERT INTO ingest_runs (tenant_id, source_type) VALUES ($1, $2)",
                    TENANT,
                    "text",
                )
        finally:
            await conn.execute("RESET ROLE")


@pytest.mark.requires_postgres
async def test_ledger_completed_run_enables_unchanged_probe(integrity_pool) -> None:
    from corpus_kb.tools.ingest_store import PostgresIngestStore

    store = PostgresIngestStore(integrity_pool, TENANT)
    doc = _document("ledger-completed")
    file_hash = "a" * 64

    doc_id, run_id = await store.begin_run(doc, file_hash)
    # Running row: probe must NOT skip.
    assert await store.unchanged_doc_id(doc.path, file_hash) is None

    await store.finish_run(run_id, file_hash, {"chunks": 1, "errors": []}, [])

    row = await _tenant_fetchrow(
        integrity_pool,
        "SELECT status, finish_hash, stages->'errors' AS errs, error, finished_at "
        "FROM ingest_runs WHERE run_id = $1::uuid",
        run_id,
    )
    assert row["status"] == "completed"
    assert row["finish_hash"] == file_hash
    # asyncpg returns jsonb as text by default: stages->'errors' is '[]'.
    assert row["errs"] == "[]"
    assert row["error"] is None
    assert row["finished_at"] is not None

    probe = await store.unchanged_doc_id(doc.path, file_hash)
    assert probe == doc_id


@pytest.mark.requires_postgres
async def test_ledger_failed_run_never_enables_probe(integrity_pool) -> None:
    from corpus_kb.tools.ingest_store import PostgresIngestStore

    store = PostgresIngestStore(integrity_pool, TENANT)
    doc = _document("ledger-failed")
    file_hash = "b" * 64

    _doc_id, run_id = await store.begin_run(doc, file_hash)
    await store.finish_run(
        run_id,
        file_hash,
        {"chunks": 0, "errors": ["EmbeddingError: down"]},
        ["EmbeddingError: down"],
    )

    row = await _tenant_fetchrow(
        integrity_pool,
        "SELECT status, finish_hash, error FROM ingest_runs WHERE run_id = $1::uuid",
        run_id,
    )
    assert row["status"] == "failed"
    assert row["finish_hash"] is None
    assert "EmbeddingError" in row["error"]
    # documents.file_hash MATCHES but the run failed: probe must not skip.
    assert await store.unchanged_doc_id(doc.path, file_hash) is None


@pytest.mark.requires_postgres
async def test_ledger_crash_leaves_running_which_never_enables_probe(
    integrity_pool,
) -> None:
    from corpus_kb.tools.ingest_store import PostgresIngestStore

    store = PostgresIngestStore(integrity_pool, TENANT)
    doc = _document("ledger-crash")
    file_hash = "c" * 64

    _doc_id, run_id = await store.begin_run(doc, file_hash)
    # Simulate a crash mid-run: nothing closes the row.
    row = await _tenant_fetchrow(
        integrity_pool,
        "SELECT status FROM ingest_runs WHERE run_id = $1::uuid",
        run_id,
    )
    assert row["status"] == "running"
    assert await store.unchanged_doc_id(doc.path, file_hash) is None

    # fail_run closes it honestly.
    await store.fail_run(run_id, "KeyboardInterrupt: ctrl-c")
    row = await _tenant_fetchrow(
        integrity_pool,
        "SELECT status, error FROM ingest_runs WHERE run_id = $1::uuid",
        run_id,
    )
    assert row["status"] == "failed"
    assert "ctrl-c" in row["error"]
    assert await store.unchanged_doc_id(doc.path, file_hash) is None


@pytest.mark.requires_postgres
async def test_advisory_lock_contention_returns_honest_skip(integrity_pool, integrity_dsn) -> None:
    import asyncpg

    import corpus_kb.tools.ingest_common as ingest_module

    source = f"lock-contention-{uuid.uuid4().hex[:8]}"
    text = "advisory lock contention probe content"
    file_hash = ingest_module._sha256(text)

    blocker = await asyncpg.connect(integrity_dsn)
    try:
        held = await blocker.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1 || ':' || $2, 0))",
            TENANT,
            file_hash,
        )
        assert held is True

        result = await ingest_module.ingest_text(
            text=text,
            pg_pool=integrity_pool,
            source_type="text",
            config={"graph": {"extract_entities": False}},
            tenant_id=TENANT,
            source=source,
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "concurrent-run"
    finally:
        await blocker.execute(
            "SELECT pg_advisory_unlock(hashtextextended($1 || ':' || $2, 0))",
            TENANT,
            file_hash,
        )
        await blocker.close()

    # Lock released: the run is admitted (it may complete, degrade, or — on
    # the live vector(4096) schema with a 768-dim embedder — raise the typed
    # width ConfigError). The point is that it is no longer the contention
    # skip: only "skipped"/"concurrent-run" would be a lie here.
    admitted_result: dict[str, object] | None = None
    admitted_config_error: ConfigError | None = None
    try:
        admitted_result = await ingest_module.ingest_text(
            text=text,
            pg_pool=integrity_pool,
            source_type="text",
            config={"graph": {"extract_entities": False}},
            tenant_id=TENANT,
            source=source,
        )
    except ConfigError as exc:
        admitted_config_error = exc
    assert admitted_config_error is None or "embedding.model" in (admitted_config_error.remediation)
    if admitted_result is not None:
        assert admitted_result["status"] in {"success", "partial"}
    run_row = await _tenant_fetchrow(
        integrity_pool,
        "SELECT status FROM ingest_runs WHERE start_hash = $1",
        file_hash,
    )
    assert run_row is not None
    assert run_row["status"] in {"completed", "failed"}


@pytest.mark.requires_postgres
async def test_store_vectors_overwrites_stale_1024_and_sets_degraded(
    integrity_pool,
) -> None:
    from corpus_kb.tools.ingest_store import PostgresIngestStore
    from corpus_kb.utils.models import Chunk

    doc = _document("vectors-overwrite")
    file_hash = "d" * 64
    store = PostgresIngestStore(integrity_pool, TENANT)
    doc_id, _run_id = await store.begin_run(doc, file_hash)

    # chunks.chunk_id is a UUID column — the model carries it as a uuid string.
    chunk_id = str(uuid.uuid4())
    chunk = Chunk(chunk_id=chunk_id, document_id=doc_id, text="vector probe", source_type="text")
    await _tenant_execute(
        integrity_pool,
        """
        INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)
        VALUES ($1, $2, $3, 0, $4)
        """,
        chunk_id,
        TENANT,
        doc_id,
        "vector probe",
    )
    # Seed a stale row: old vector_1024 slice + healthy degraded flag.
    v2560 = "[" + ",".join("0.25" for _ in range(2560)) + "]"
    stale_1024 = "[" + ",".join("0.5" for _ in range(1024)) + "]"
    await _tenant_execute(
        integrity_pool,
        """
        INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding, vector_1024,
            embedding_model, dimensions, degraded)
        VALUES ($1, $2, $3::halfvec, $4::vector, 'old-model', 2560, FALSE)
        """,
        chunk_id,
        TENANT,
        v2560,
        stale_1024,
    )

    # Re-embed: fresh 768-slot-free write with NO matryoshka map. The stale
    # vector_1024 must be OVERWRITTEN (NULL), not COALESCEd, and the degraded
    # flag must state this write honestly (real vector -> FALSE).
    chunk.embedding = [0.125] * 2560
    written = await store.store_vectors([chunk], "new-model", dimensions=2560)
    assert written == 1
    row = await _tenant_fetchrow(
        integrity_pool,
        "SELECT vector_1024, embedding_model, degraded FROM chunks_vectors WHERE chunk_id = $1",
        chunk_id,
    )
    assert row["vector_1024"] is None
    assert row["embedding_model"] == "new-model"
    assert row["degraded"] is False
    chunk_row = await _tenant_fetchrow(
        integrity_pool, "SELECT degraded FROM chunks WHERE chunk_id = $1", chunk_id
    )
    assert chunk_row["degraded"] is False

    # An all-zero embedder output poisons: degraded must flip on BOTH rows.
    chunk.embedding = [0.0] * 2560
    await store.store_vectors([chunk], "new-model", dimensions=2560)
    row = await _tenant_fetchrow(
        integrity_pool,
        "SELECT degraded FROM chunks_vectors WHERE chunk_id = $1",
        chunk_id,
    )
    assert row["degraded"] is True
    chunk_row = await _tenant_fetchrow(
        integrity_pool, "SELECT degraded FROM chunks WHERE chunk_id = $1", chunk_id
    )
    assert chunk_row["degraded"] is True


@pytest.mark.requires_postgres
async def test_migration_022_rollback_and_reapply(integrity_pool, integrity_dsn) -> None:
    """rollback_022 drops the ledger + degraded columns; re-applying 022
    restores them. Must stay LAST in this file (it drops objects other tests
    use). Convention: tests/test_es_foundation.py::test_migration_016_rollback_and_reapply."""
    import asyncpg

    await integrity_pool.execute(ROLLBACK_022.read_text(encoding="utf-8"))
    conn = await asyncpg.connect(integrity_dsn)
    try:
        table = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE tablename = 'ingest_runs'"
        )
        assert table == 0, "rollback_022 did not drop ingest_runs"
        col = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'chunks_vectors' AND column_name = 'degraded'"
        )
        assert col == 0, "rollback_022 did not drop chunks_vectors.degraded"
    finally:
        await conn.close()

    # Re-apply: objects restored (twice, proving idempotency of the chain).
    await integrity_pool.execute(MIGRATION_022.read_text(encoding="utf-8"))
    await integrity_pool.execute(MIGRATION_022.read_text(encoding="utf-8"))
    conn = await asyncpg.connect(integrity_dsn)
    try:
        table = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE tablename = 'ingest_runs'"
        )
        assert table == 1
        forced = await conn.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'ingest_runs'"
        )
        assert forced is True
    finally:
        await conn.close()


def _document(source: str) -> Any:
    from corpus_kb.utils.models import Document

    return Document(
        path=source,
        source_type="text",
        content="probe content",
        size_bytes=len(b"probe content"),
    )


# ---------------------------------------------------------------------------
# Live-PG FORCE-RLS verification of the #87 v1-store migration (closes #75)
# ---------------------------------------------------------------------------

# Fully static DSN literal (no interpolation) for the FORCE-RLS scratch DB.
IR075_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_ir075_force_rls_test"

# Same extension-gated skips as the shadow harness (004/005 need AGE/pgml
# server packages) — but 011/013 are the POINT here: FORCE RLS is exactly the
# condition the migrated store methods must survive as a non-owner role.
_IR075_EXTENSION_GATED = {"004_enable_age.sql", "005_enable_pgml.sql"}


class _SingleConnPool:
    """Pool shim so PostgresIngestStore can drive one live connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> _SingleConnPool:
        return self

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


async def _create_force_rls_db() -> str:
    import asyncpg

    admin = await asyncpg.connect(SUPER_DSN)
    try:
        await admin.execute('DROP DATABASE IF EXISTS "corpus_kb_ir075_force_rls_test" WITH (FORCE)')
        await admin.execute('CREATE DATABASE "corpus_kb_ir075_force_rls_test" OWNER corpus_user')
        # Cluster-wide non-owner role for the RLS probe (idempotent).
        await admin.execute(
            """
            DO $$
            BEGIN
                CREATE ROLE corpus_ir075_probe LOGIN PASSWORD 'probe_ir075';
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """
        )
        # Extensions need superuser; run them against the scratch DB.
        scratch = await asyncpg.connect(super_dsn_for("corpus_kb_ir075_force_rls_test"))
        try:
            await scratch.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await scratch.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        finally:
            await scratch.close()
    finally:
        await admin.close()

    owner = await asyncpg.connect(IR075_DSN)
    try:
        await owner.execute(
            "GRANT CONNECT ON DATABASE corpus_kb_ir075_force_rls_test TO corpus_ir075_probe"
        )
        # The halfvec(2560) schema carries only LEGAL indexes.
        await owner.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        from corpus_kb._setup.migrate import ensure_migrations_table

        await ensure_migrations_table(owner)
        for path in sorted(MIGRATIONS.iterdir()):
            if path.suffix != ".sql" or not path.name[0].isdigit():
                continue
            if path.name in _IR075_EXTENSION_GATED:
                continue
            await owner.execute(path.read_text(encoding="utf-8"))
            await owner.execute(
                "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)"
                " ON CONFLICT DO NOTHING",
                path.name,
            )
        # Unlike the shadow harness, FORCE stays ON for chunks/chunks_vectors:
        # that is the condition under test. Grant the probe role its writes.
        await owner.execute("GRANT USAGE ON SCHEMA corpus TO corpus_ir075_probe")
        await owner.execute(
            "GRANT SELECT, INSERT, UPDATE ON documents, chunks, chunks_vectors"
            " TO corpus_ir075_probe"
        )
    finally:
        await owner.close()
    return IR075_DSN


@pytest.fixture(scope="module")
def ir075_dsn() -> str:
    try:
        return asyncio.run(_create_force_rls_db())
    except (OSError, asyncpg.PostgresError, ImportError) as exc:
        pytest.skip(f"Postgres unavailable for the FORCE-RLS scratch DB: {exc}")


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_v1_store_chunks_succeed_under_force_rls_as_non_owner(ir075_dsn: str) -> None:
    """The #75 named acceptance: against FORCE-RLS chunks, the migrated
    store_chunks succeeds as a NON-OWNER role — proof the tenant GUC is set
    inside the transaction instead of evaporating on autocommit. A bare
    INSERT without the GUC violates RLS (negative control), so any
    regression to the old pattern fails this test loudly."""
    import asyncpg

    from corpus_kb.tools.ingest_store import PostgresIngestStore
    from corpus_kb.utils.models import Chunk

    doc_id = uuid.UUID("00000000-0000-0000-0000-0000000000d0")
    owner = await asyncpg.connect(ir075_dsn)
    probe = await asyncpg.connect(ir075_dsn, user="corpus_ir075_probe", password="probe_ir075")
    try:
        forced = await owner.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'chunks'"
        )
        assert forced is True, "chunks must be FORCE RLS for this test to mean anything"

        # Seed the parent document as owner (owner is an RLS subject too).
        async with owner.transaction():
            await owner.execute("SELECT set_config('app.current_tenant_id', $1, true)", TENANT)
            await owner.execute(
                "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
                " VALUES ($1, $2, $3, $4)",
                doc_id,
                uuid.UUID(TENANT),
                "rls-probe.py",
                "code",
            )

        # Negative control: the probe role's bare write (no tenant GUC) must
        # be rejected — otherwise this harness proves nothing.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await probe.execute(
                "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
                " VALUES ($1, $2, $3, $4)",
                uuid.UUID("00000000-0000-0000-0000-0000000000e0"),
                uuid.UUID(TENANT),
                "rls-probe-bare.py",
                "code",
            )

        # The migrated path: the store sets the GUC inside its transaction.
        store = PostgresIngestStore(_SingleConnPool(probe), TENANT)
        chunk_id = "00000000-0000-0000-0000-0000000000c1"
        chunk = Chunk(chunk_id=chunk_id, document_id=str(doc_id), text="x", source_type="text")
        chunk.embedding = None
        needs = await store.store_chunks([chunk])
        assert needs == {chunk_id}

        # Under FORCE RLS even the owner is an RLS subject: the verifying
        # count must run with the tenant GUC set, exactly like the seed.
        async with owner.transaction():
            await owner.execute("SELECT set_config('app.current_tenant_id', $1, true)", TENANT)
            count = await owner.fetchval(
                "SELECT count(*) FROM chunks WHERE tenant_id = $1", uuid.UUID(TENANT)
            )
        assert count == 1, "the probe role's chunk must be persisted under FORCE RLS"
    finally:
        await probe.close()
        await owner.close()
