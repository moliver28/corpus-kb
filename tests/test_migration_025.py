"""Migration 025 live verification: apply, idempotency, rollback (W1 exit).

Runs against a THROWAWAY database (drop/create per module): applies
storage/schema.sql + migrations 001+ (skipping the extension-gated 004/005
AGE/pgml pair, per the research_db.py precedent), then drives the
migration-025 regime end to end:

* apply 025 on the OLD-shape fixture data: the main column becomes
  ``embedding halfvec(2560)``, old vectors become NULL (stale), the
  matryoshka slice is dropped for stale rows;
* re-apply is a no-op (the runner records by filename, so idempotency is
  proven by running the FILE twice);
* the rollback restores the vector(4096) SHAPE (data cannot come back) and
  migration 025 re-applies cleanly afterwards.

Auto-skips when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.requires_postgres

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"
MIGRATION_025 = MIGRATIONS / "025_halfvec_2560.sql"
ROLLBACK_025 = MIGRATIONS / "rollback_025_halfvec_2560.sql"

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
DB_NAME = "corpus_kb_mig025_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
SUPER_DSN = f"{BASE_DSN}/postgres"
TENANT = "00000000-0000-0000-0000-000000000001"

# The AGE/pgml migrations need server packages the test host may lack
# (research_db.py precedent).
_EXTENSION_GATED = {"004_enable_age.sql", "005_enable_pgml.sql"}


async def _column_type(conn: asyncpg.Connection, table: str, column: str) -> str | None:
    row = await conn.fetchrow(
        """
        SELECT format_type(a.atttypid, a.atttypmod) AS typ
        FROM pg_attribute a
        WHERE a.attrelid = ('public.' || $1)::regclass AND a.attname = $2
        """,
        table,
        column,
    )
    return None if row is None else str(row["typ"])


def _vector_literal(values: list[float], dims: int) -> str:
    padded = values + [0.0] * (dims - len(values))
    return "[" + ",".join(str(v) for v in padded[:dims]) + "]"


async def _bootstrap_old_shape() -> asyncpg.Connection:
    """Scratch DB at the OLD (pre-025) shape, with fixture vectors in it."""
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
    # Old-shape schema: this branch's ORIGINAL schema.sql content is gone
    # (the file now ships the halfvec end state), so reconstruct the 4096
    # shape by applying the new schema then rewinding the column DDL — the
    # exact transformation migration 025 declares as its start state.
    await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    await conn.execute("DROP INDEX IF EXISTS idx_chunks_vectors_hnsw_halfvec2560")
    await conn.execute(
        "ALTER TABLE chunks_vectors ALTER COLUMN embedding TYPE vector(4096) USING NULL"
    )
    await conn.execute("ALTER TABLE chunks_vectors RENAME COLUMN embedding TO vector")
    await conn.execute(
        "ALTER TABLE chunks_vectors ALTER COLUMN embedding_model SET DEFAULT 'nomic-embed-text'"
    )
    await conn.execute(
        "ALTER TABLE chunks_vectors ADD CONSTRAINT vector_not_null CHECK (vector IS NOT NULL)"
    )

    from corpus_kb._setup.migrate import ensure_migrations_table

    await ensure_migrations_table(conn)
    for path in sorted(MIGRATIONS.iterdir()):
        if path.suffix != ".sql" or not path.name[0].isdigit():
            continue
        if path.name in _EXTENSION_GATED or path.name.startswith("025_"):
            continue
        await conn.execute(path.read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO corpus.schema_migrations (filename) VALUES ($1) ON CONFLICT DO NOTHING",
            path.name,
        )

    # Fixture data at the old shape: one live row (vector + slice).
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", TENANT)
    await conn.execute(
        """
        INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count)
        VALUES ('11111111-1111-1111-1111-111111111111', $1, 'mig025-fixture', 'text', 1)
        ON CONFLICT DO NOTHING
        """,
        TENANT,
    )
    await conn.execute(
        """
        INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)
        VALUES ('22222222-2222-2222-2222-222222222222', $1,
                '11111111-1111-1111-1111-111111111111', 0, 'migration 025 fixture')
        ON CONFLICT DO NOTHING
        """,
        TENANT,
    )
    await conn.execute(
        "INSERT INTO chunks_vectors (chunk_id, tenant_id, vector, embedding_model, dimensions)"
        " VALUES ('22222222-2222-2222-2222-222222222222', $1, $2::vector, 'old-model', 4096)"
        " ON CONFLICT DO NOTHING",
        TENANT,
        _vector_literal([0.5, 0.5], 4096),
    )
    await conn.execute(
        "UPDATE chunks_vectors SET vector_1024 = l2_normalize(subvector(vector, 1, 1024))"
        " WHERE chunk_id = '22222222-2222-2222-2222-222222222222'"
    )
    await conn.close()


_BOOTSTRAP_STATE: dict[str, bool] = {"done": False}


@pytest.fixture(scope="module")
def dsn() -> str:
    """Module-scoped scratch database; each test opens its OWN connection.

    A module-scoped asyncpg connection cannot cross pytest-asyncio's
    function-scoped event loops ("another operation is in progress"), so
    the bootstrap runs once in its own loop and the tests share only the
    DSN + the mutated database state.
    """
    if not _BOOTSTRAP_STATE["done"]:
        try:
            asyncio.run(_bootstrap_old_shape())
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
async def seeded_db(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn)
    try:
        yield conn
    finally:
        await conn.close()


async def test_025_applies_and_retypes(seeded_db: asyncpg.Connection) -> None:
    conn = seeded_db
    assert await _column_type(conn, "chunks_vectors", "vector") == "vector(4096)"
    await conn.execute(MIGRATION_025.read_text(encoding="utf-8"))

    assert await _column_type(conn, "chunks_vectors", "embedding") == "halfvec(2560)"
    assert await _column_type(conn, "chunks_vectors", "vector") is None
    default = await conn.fetchval(
        "SELECT column_default FROM information_schema.columns"
        " WHERE table_name = 'chunks_vectors' AND column_name = 'embedding_model'"
    )
    assert "qwen3-embedding:4b" in str(default)

    # Old vectors became stale; the matryoshka slice went with them.
    row = await conn.fetchrow(
        "SELECT embedding, vector_1024 FROM chunks_vectors"
        " WHERE chunk_id = '22222222-2222-2222-2222-222222222222'"
    )
    assert row is not None and row["embedding"] is None
    assert row["vector_1024"] is None

    # The real hnsw index exists with the configured options.
    reloptions = await conn.fetchval(
        "SELECT reloptions FROM pg_class WHERE relname = 'idx_chunks_vectors_hnsw_halfvec2560'"
    )
    assert reloptions is not None
    opts = " ".join(str(o) for o in reloptions)
    assert "m=16" in opts and "ef_construction=128" in opts

    # Coding columns retyped.
    assert await _column_type(conn, "code_registry", "definition_vector") == "halfvec(2560)"
    assert await _column_type(conn, "code_registry", "probe_vector") == "halfvec(2560)"
    assert await _column_type(conn, "answer_ctx_vectors", "vector") == "halfvec(2560)"
    assert await _column_type(conn, "chunk_codes", "rationale_vector") == "halfvec(2560)"

    # The research grain is untouched (1024-dim vector contract).
    research_type = await _column_type(conn, "research_units", "embedding")
    assert research_type == "vector(1024)"

    # A fresh 2560-dim write lands (the new regime accepts the new width).
    await conn.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)"
        " VALUES ('33333333-3333-3333-3333-333333333333', $1,"
        " '11111111-1111-1111-1111-111111111111', 1, 'post-migration write')"
        " ON CONFLICT DO NOTHING",
        TENANT,
    )
    await conn.execute(
        "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding, embedding_model, dimensions)"
        " VALUES ('33333333-3333-3333-3333-333333333333', $1, $2::halfvec,"
        " 'qwen3-embedding:4b', 2560) ON CONFLICT DO NOTHING",
        TENANT,
        _vector_literal([1.0], 2560),
    )
    live = await conn.fetchval(
        "SELECT embedding FROM chunks_vectors"
        " WHERE chunk_id = '33333333-3333-3333-3333-333333333333'"
    )
    assert live is not None


async def test_025_reapply_is_idempotent(seeded_db: asyncpg.Connection) -> None:
    conn = seeded_db
    # Run the FILE a second time: every guard must hold (the runner records
    # by filename and would skip, but ops rehearse by re-running files).
    await conn.execute(MIGRATION_025.read_text(encoding="utf-8"))
    assert await _column_type(conn, "chunks_vectors", "embedding") == "halfvec(2560)"
    count = await conn.fetchval("SELECT count(*) FROM chunks_vectors")
    assert int(count) == 2


async def test_025_rollback_restores_shape_then_reapplies(seeded_db: asyncpg.Connection) -> None:
    conn = seeded_db
    await conn.execute(ROLLBACK_025.read_text(encoding="utf-8"))
    assert await _column_type(conn, "chunks_vectors", "vector") == "vector(4096)"
    assert await _column_type(conn, "chunks_vectors", "embedding") is None
    default = await conn.fetchval(
        "SELECT column_default FROM information_schema.columns"
        " WHERE table_name = 'chunks_vectors' AND column_name = 'embedding_model'"
    )
    assert "nomic-embed-text" in str(default)
    # Data did NOT come back (one-way retype, by design).
    count = await conn.fetchval("SELECT count(*) FROM chunks_vectors WHERE vector IS NOT NULL")
    assert int(count) == 0
    # The coding columns follow the same shape restoration.
    assert await _column_type(conn, "code_registry", "definition_vector") == "vector(4096)"

    # Forward again: the migration re-applies on the rolled-back shape.
    await conn.execute(MIGRATION_025.read_text(encoding="utf-8"))
    assert await _column_type(conn, "chunks_vectors", "embedding") == "halfvec(2560)"
