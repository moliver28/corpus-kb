"""Migration 027 live verification: dates rule, backfill op, forward upsert.

Runs against a THROWAWAY database (drop/create per module): applies
storage/schema.sql + migrations 001+ (skipping the extension-gated 004/005
AGE/pgml pair and 027 itself, per the test_migration_026 precedent), then
drives the WP-1 date-provenance seams end to end:

* 027 adds the nullable ``documents.document_date`` column + index;
  re-apply is a no-op; the rollback drops both cleanly;
* the migration's backfill answers the rule: per document the earliest
  of published_time, then modified_time, then the chunk source_timestamp;
  a malformed string degrades to the next candidate, never an abort;
* ``analysis_backfill_dates`` is idempotent (second run dates nothing)
  and tenant-scoped (the other tenant's documents stay untouched);
* the forward upsert writes and preserves ``document_date`` per the
  COALESCE rule (a new date fills the column, a NULL never clears it).

Auto-skips when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from corpus_kb.analysis.dates import document_dates_present
from corpus_kb.tools.analysis_tools import analysis_backfill_dates
from corpus_kb.tools.ingest_store import PostgresIngestStore
from corpus_kb.utils.models import Document

pytestmark = pytest.mark.requires_postgres

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"
MIGRATION_027 = MIGRATIONS / "027_document_dates.sql"
ROLLBACK_027 = MIGRATIONS / "rollback_027_document_dates.sql"

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
DB_NAME = "corpus_kb_mig027_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
SUPER_DSN = f"{BASE_DSN}/postgres"
TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
OVER_TIME_ON = {"analysis": {"topics": {"over_time": {"enabled": True}}}}

TS_MAR = datetime(2024, 3, 1, 10, 0, tzinfo=UTC)
TS_MAY_EARLY = datetime(2024, 5, 1, tzinfo=UTC)
TS_MAY_LATE = datetime(2024, 5, 10, tzinfo=UTC)

_ChunkSpec = tuple[int, dict[str, object], datetime | None]
_DocSpec = tuple[str, str, tuple[_ChunkSpec, ...]]

# (doc_id, source, [(chunk_index, metadata, source_timestamp), ...]).
_DATED_CORPUS: tuple[_DocSpec, ...] = (
    (
        "33333333-3333-3333-3333-333333333301",
        "d1-published",
        ((0, {"published_time": "2024-03-01T10:00:00Z"}, None),),
    ),
    (
        "33333333-3333-3333-3333-333333333302",
        "d2-modified",
        ((0, {"modified_time": "2024-06-15"}, None),),
    ),
    (
        "33333333-3333-3333-3333-333333333303",
        "d3-source-ts",
        ((0, {}, TS_MAY_LATE), (1, {}, TS_MAY_EARLY)),
    ),
    (
        "33333333-3333-3333-3333-333333333304",
        "d4-malformed",
        (
            (
                0,
                {"published_time": "coming soon", "modified_time": "2024-07-04T12:00:00+02:00"},
                None,
            ),
        ),
    ),
    ("33333333-3333-3333-3333-333333333305", "d5-undated", ((0, {}, None),)),
)
OTHER_DOC = "33333333-3333-3333-3333-333333333310"

_EXTENSION_GATED = {"004_enable_age.sql", "005_enable_pgml.sql"}
_SKIP_AT_BOOTSTRAP = _EXTENSION_GATED | {"027_document_dates.sql"}


async def _bootstrap() -> None:
    """Scratch DB at the current schema + migrations 001-029 (minus 027, AGE/pgml)."""
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        statements = {
            "drop": f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)',
            "create": f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user',
        }
        await admin.execute(statements["drop"])
        await admin.execute(statements["create"])
    finally:
        await admin.close()

    superconn = await asyncpg.connect(DSN, user="postgres", password="postgres")
    try:
        await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    finally:
        await superconn.close()

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        from corpus_kb._setup.migrate import ensure_migrations_table

        await ensure_migrations_table(conn)
        for path in sorted(MIGRATIONS.iterdir()):
            if path.suffix != ".sql" or not path.name[0].isdigit():
                continue
            if path.name in _SKIP_AT_BOOTSTRAP:
                continue
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)"
                " ON CONFLICT DO NOTHING",
                path.name,
            )
        await _seed_corpus(conn)
    finally:
        await conn.close()


async def _seed_corpus(conn: asyncpg.Connection) -> None:
    """Documents whose chunk metadata exercises every branch of the rule."""
    for tenant in (TENANT, OTHER_TENANT):
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant)
        docs: tuple[_DocSpec, ...]
        if tenant == TENANT:
            docs = _DATED_CORPUS
        else:
            docs = ((OTHER_DOC, "other-published", ((0, {"published_time": "2025-01-01"}, None),)),)
        for doc_id, source, chunks in docs:
            await conn.execute(
                """
                INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count)
                VALUES ($2, $1, $3, 'text', $4)
                ON CONFLICT DO NOTHING
                """,
                tenant,
                doc_id,
                source,
                len(chunks),
            )
            for index, metadata, source_timestamp in chunks:
                chunk_id = _chunk_id(doc_id, index)
                await conn.execute(
                    """
                    INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index,
                        text, metadata, source_timestamp)
                    VALUES ($2, $1, $3, $4, $5, $6::jsonb, $7)
                    ON CONFLICT DO NOTHING
                    """,
                    tenant,
                    chunk_id,
                    doc_id,
                    index,
                    f"date fixture chunk {index}",
                    json.dumps(metadata),
                    source_timestamp,
                )
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", TENANT)


def _chunk_id(doc_id: str, index: int) -> str:
    """One deterministic valid UUID per (document, chunk index)."""
    suffix = f"{doc_id.replace('-', '')[-10:]}{index:02d}"
    return f"44444444-4444-4444-4444-{suffix}"


async def _apply_027(conn: asyncpg.Connection) -> None:
    """Apply the migration with stored tenant dates reset.

    Order matters: the column must exist before the reset, and the second
    apply lets the migration's DO block recompute every tenant date from
    scratch (the IS DISTINCT FROM guard keeps it a no-op otherwise). The
    session timezone is pinned to UTC first: the migration's DO block
    reads a NAIVE candidate at the session TimeZone, and the backfill op
    pins UTC (analysis/dates.py) — the pin keeps this rule check
    host-independent.
    """
    await conn.execute("SET timezone = 'UTC'")
    await conn.execute(MIGRATION_027.read_text(encoding="utf-8"))
    await conn.execute("UPDATE documents SET document_date = NULL WHERE tenant_id = $1", TENANT)
    await conn.execute(MIGRATION_027.read_text(encoding="utf-8"))


_BOOTSTRAP_STATE: dict[str, bool] = {"done": False}


@pytest.fixture(scope="module")
def dsn() -> str:
    """Module-scoped scratch database; each test opens its OWN connection."""
    if not _BOOTSTRAP_STATE["done"]:
        try:
            asyncio.run(_bootstrap())
        except Exception as exc:
            pytest.skip(f"Postgres not available or bootstrap failed: {exc}")
            raise
        _BOOTSTRAP_STATE["done"] = True
    yield DSN
    if _BOOTSTRAP_STATE["done"]:
        asyncio.run(_drop_scratch_db())
        _BOOTSTRAP_STATE["done"] = False


async def _drop_scratch_db() -> None:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        statements = {"drop": f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)'}
        await admin.execute(statements["drop"])
    finally:
        await admin.close()


@pytest.fixture
async def conn(dsn: str) -> asyncpg.Connection:
    connection = await asyncpg.connect(dsn)
    try:
        yield connection
    finally:
        await connection.close()


def _pool(conn: asyncpg.Connection) -> _SingleConnPool:
    return _SingleConnPool(conn)


class _SingleConnPool:
    """Wraps one live connection behind the acquire() contract."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self._conn = connection

    def acquire(self) -> _SingleConnPool:
        return self

    async def __aenter__(self) -> asyncpg.Connection:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def close(self) -> None:
        return None


async def _document_date(conn: asyncpg.Connection, tenant: str, source: str) -> datetime | None:
    """The stored date for one tenant's document (tenant GUC inside one transaction)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant)
        row = await conn.fetchrow(
            "SELECT document_date FROM documents WHERE tenant_id = $1 AND source = $2",
            tenant,
            source,
        )
    return None if row is None else row["document_date"]


async def test_027_applies_and_is_idempotent(conn: asyncpg.Connection) -> None:
    await _apply_027(conn)
    column = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'documents'"
        " AND column_name = 'document_date'"
    )
    assert int(column) == 1
    index = await conn.fetchval(
        "SELECT count(*) FROM pg_indexes"
        " WHERE schemaname = 'public' AND indexname = 'idx_documents_tenant_date'"
    )
    assert int(index) == 1
    # Run the FILE a second time: every guard must hold.
    await conn.execute(MIGRATION_027.read_text(encoding="utf-8"))
    assert await document_dates_present(_pool(conn)) is True


async def test_027_rollback_drops_and_reapplies(conn: asyncpg.Connection) -> None:
    await _apply_027(conn)
    await conn.execute(ROLLBACK_027.read_text(encoding="utf-8"))
    column = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'documents'"
        " AND column_name = 'document_date'"
    )
    assert int(column) == 0
    # Forward again on the rolled-back shape.
    await conn.execute(MIGRATION_027.read_text(encoding="utf-8"))
    assert await document_dates_present(_pool(conn)) is True


async def test_027_backfill_follows_the_migration_rule(conn: asyncpg.Connection) -> None:
    await _apply_027(conn)
    assert await _document_date(conn, TENANT, "d1-published") == TS_MAR
    assert await _document_date(conn, TENANT, "d2-modified") == datetime(2024, 6, 15, tzinfo=UTC)
    assert await _document_date(conn, TENANT, "d3-source-ts") == TS_MAY_EARLY
    assert await _document_date(conn, TENANT, "d4-malformed") == datetime(
        2024, 7, 4, 10, 0, tzinfo=UTC
    )
    assert await _document_date(conn, TENANT, "d5-undated") is None
    # The migration file scopes itself to the seed tenant (its SET at the top).
    assert await _document_date(conn, OTHER_TENANT, "other-published") is None


async def test_backfill_op_is_idempotent_and_tenant_scoped(
    conn: asyncpg.Connection,
) -> None:
    await _apply_027(conn)
    # The op's use case: dates unknown at migration time (new or legacy
    # documents) get filled; the migration itself already did its share.
    await conn.execute("UPDATE documents SET document_date = NULL WHERE tenant_id = $1", TENANT)
    result = await analysis_backfill_dates(_pool(conn), config=OVER_TIME_ON, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["documents_scanned"] == 5
    assert result["documents_dated"] == 4
    assert result["params"] == {"rule_version": 1}
    assert await _document_date(conn, TENANT, "d1-published") == TS_MAR
    assert await _document_date(conn, TENANT, "d4-malformed") == datetime(
        2024, 7, 4, 10, 0, tzinfo=UTC
    )
    assert await _document_date(conn, TENANT, "d5-undated") is None
    assert await _document_date(conn, OTHER_TENANT, "other-published") is None

    # Idempotent: a second run scans the same documents and dates none.
    again = await analysis_backfill_dates(_pool(conn), config=OVER_TIME_ON, tenant_id=TENANT)
    assert again["status"] == "success"
    assert again["documents_scanned"] == 5
    assert again["documents_dated"] == 0

    # Tenant-scoped: the other tenant's backfill is its own run.
    other = await analysis_backfill_dates(_pool(conn), config=OVER_TIME_ON, tenant_id=OTHER_TENANT)
    assert other["documents_scanned"] == 1
    assert other["documents_dated"] == 1
    assert await _document_date(conn, OTHER_TENANT, "other-published") == datetime(
        2025, 1, 1, tzinfo=UTC
    )


async def test_forward_upsert_writes_and_preserves_document_date(
    conn: asyncpg.Connection,
) -> None:
    await _apply_027(conn)
    store = PostgresIngestStore(_pool(conn), TENANT)

    dated = Document(
        path="dates-fwd-doc-a",
        source_type="text",
        content="forward date fixture",
        size_bytes=20,
        metadata={"document_date": "2024-05-01T00:00:00+00:00"},
    )
    _doc_id, _run_id = await store.begin_run(dated, "hash-fwd-a")
    assert await _document_date(conn, TENANT, "dates-fwd-doc-a") == TS_MAY_EARLY

    # A re-upsert with NO resolved date never clears the stored one.
    await store.update_document(dated.model_copy(update={"metadata": {}}), "hash-fwd-a")
    assert await _document_date(conn, TENANT, "dates-fwd-doc-a") == TS_MAY_EARLY

    # A newly resolved date replaces the stored one (same source, conflict path).
    await store.update_document(
        dated.model_copy(update={"metadata": {"document_date": "2024-07-01T05:00:00Z"}}),
        "hash-fwd-a",
    )
    assert await _document_date(conn, TENANT, "dates-fwd-doc-a") == datetime(
        2024, 7, 1, 5, 0, tzinfo=UTC
    )
    await store.update_document(dated.model_copy(update={"metadata": {}}), "hash-fwd-a")
    assert await _document_date(conn, TENANT, "dates-fwd-doc-a") == datetime(
        2024, 7, 1, 5, 0, tzinfo=UTC
    )

    # A document with no date anywhere stays honestly NULL.
    plain = Document(
        path="dates-fwd-doc-b",
        source_type="text",
        content="undated forward fixture",
        size_bytes=23,
    )
    await store.begin_run(plain, "hash-fwd-b")
    assert await _document_date(conn, TENANT, "dates-fwd-doc-b") is None
