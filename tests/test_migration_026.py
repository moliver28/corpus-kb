"""Migration 026 live verification + the topics writer round-trip (W4 exit).

Runs against a THROWAWAY database (drop/create per module): applies
storage/schema.sql + migrations 001+ (skipping the extension-gated 004/005
AGE/pgml pair, per the test_migration_025 precedent), then drives the
topics layer end to end:

* 026 creates the two tables; re-apply is a no-op;
* the rollback drops them cleanly and 026 re-applies afterwards;
* ``write_build`` replaces the tenant's rows wholesale and RLS honors
  the tenant GUC (another tenant reads nothing).

Auto-skips when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
import pytest

from corpus_kb.topics.writer import TopicRow, read_topics, topics_tables_present, write_build

pytestmark = pytest.mark.requires_postgres

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"
MIGRATION_026 = MIGRATIONS / "026_topics.sql"
ROLLBACK_026 = MIGRATIONS / "rollback_026_topics.sql"

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
DB_NAME = "corpus_kb_mig026_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
SUPER_DSN = f"{BASE_DSN}/postgres"
TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
DOC_ID = "11111111-1111-1111-1111-111111111111"

_EXTENSION_GATED = {"004_enable_age.sql", "005_enable_pgml.sql"}


async def _bootstrap() -> None:
    """Scratch DB at the current schema + migrations 001-026 (minus AGE/pgml)."""
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user')
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
            if path.name in _EXTENSION_GATED or path.name.startswith("026_"):
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
    """One document with two embedded chunks (the assignment targets)."""
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", TENANT)
    await conn.execute(
        """
        INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count)
        VALUES ($2, $1, 'mig026-fixture', 'text', 2)
        ON CONFLICT DO NOTHING
        """,
        TENANT,
        DOC_ID,
    )
    for index in range(2):
        chunk_id = f"22222222-2222-2222-2222-22222222222{index}"
        await conn.execute(
            """
            INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)
            VALUES ($2, $1, $3, $4, $5)
            ON CONFLICT DO NOTHING
            """,
            TENANT,
            chunk_id,
            DOC_ID,
            index,
            f"topic fixture chunk {index}",
        )
        vector = "[" + ",".join(["1.0" if index == 0 else "0.5"] + ["0.1"] * 31) + "]"
        padded = vector[:-1] + "," + ",".join(["0.0"] * (2560 - 32)) + "]"
        await conn.execute(
            """
            INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding, embedding_model, dimensions)
            VALUES ($2, $1, $3::halfvec, 'qwen3-embedding:4b', 2560)
            ON CONFLICT DO NOTHING
            """,
            TENANT,
            chunk_id,
            padded,
        )


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
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
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


TWO_TOPICS = [
    TopicRow(
        topic_index=0,
        label="0_alpha_signals",
        reduced_label=None,
        size=2,
        keywords=[["signal", 0.9]],
        params={"umap_random_state": 42},
    ),
    TopicRow(
        topic_index=1,
        label="1_beta_rules",
        reduced_label="Retrieval rules",
        size=1,
        keywords=[["rule", 0.8]],
        params={},
    ),
]
TWO_ASSIGNMENTS = [
    ("22222222-2222-2222-2222-222222222220", 0, 0.9),
    ("22222222-2222-2222-2222-222222222221", 1, 0.8),
]


async def test_026_applies_and_is_idempotent(conn: asyncpg.Connection) -> None:
    await conn.execute(MIGRATION_026.read_text(encoding="utf-8"))
    tables = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables"
        " WHERE table_schema = 'public' AND table_name IN ('topics', 'chunk_topics')"
    )
    assert int(tables) == 2
    forced = await conn.fetchval(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'public' AND c.relname IN ('topics', 'chunk_topics')"
        " AND c.relrowsecurity AND c.relforcerowsecurity"
    )
    assert int(forced) == 2
    # Run the FILE a second time: every guard must hold.
    await conn.execute(MIGRATION_026.read_text(encoding="utf-8"))
    tables_after = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables"
        " WHERE table_schema = 'public' AND table_name IN ('topics', 'chunk_topics')"
    )
    assert int(tables_after) == 2


async def test_026_rollback_drops_and_reapplies(conn: asyncpg.Connection) -> None:
    await conn.execute(MIGRATION_026.read_text(encoding="utf-8"))
    await conn.execute(ROLLBACK_026.read_text(encoding="utf-8"))
    remaining = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables"
        " WHERE table_schema = 'public' AND table_name IN ('topics', 'chunk_topics')"
    )
    assert int(remaining) == 0
    # Forward again on the rolled-back shape.
    await conn.execute(MIGRATION_026.read_text(encoding="utf-8"))
    assert await topics_tables_present(_pool(conn)) is True


async def test_writer_round_trip_and_wholesale_replace(conn: asyncpg.Connection) -> None:
    await conn.execute(MIGRATION_026.read_text(encoding="utf-8"))
    pool = _pool(conn)
    written = await write_build(pool, TENANT, TWO_TOPICS, TWO_ASSIGNMENTS)
    assert written["topics_written"] == 2
    assert written["assignments_written"] == 2

    rows = await read_topics(pool, TENANT)
    assert len(rows) == 2
    by_label = {str(row["label"]): row for row in rows}
    alpha = by_label["0_alpha_signals"]
    assert alpha["size"] == 2
    assert alpha["reduced_label"] is None
    assert alpha["keywords"] == [["signal", 0.9]]
    assert alpha["params"]["umap_random_state"] == 42
    assert by_label["1_beta_rules"]["reduced_label"] == "Retrieval rules"

    # Wholesale replace: the second build leaves exactly its own rows.
    second = [
        TopicRow(
            topic_index=0, label="0_merged", reduced_label="All", size=2, keywords=[], params={}
        )
    ]
    second_assignments = [
        ("22222222-2222-2222-2222-222222222220", 0, 0.9),
        ("22222222-2222-2222-2222-222222222221", 0, 0.8),
    ]
    await write_build(pool, TENANT, second, second_assignments)
    rows_after = await read_topics(pool, TENANT)
    assert [str(row["label"]) for row in rows_after] == ["0_merged"]

    # RLS: inside one tenant-GUC transaction, the other tenant reads
    # nothing (FORCE RLS honors the in-transaction GUC only).
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", OTHER_TENANT)
        other = await conn.fetch("SELECT count(*) AS n FROM topics")
    assert int(other[0]["n"]) == 0
