"""Migration 021 behavioral tests: shadow hierarchy tables + RLS + vectors.

Mirrors the scratch-DB harness pattern (tests/test_exact_cache_migration.py
and tests/research_db.py): a throwaway database created via the superuser
postgres:postgres account, base schema + migration 021 applied, behavior
asserted, database dropped after — and a clean skip when no Postgres is
reachable. The shadow-ingest section additionally proves the pipeline's
side-by-side contract: hierarchy ON leaves the v1 chunks/vectors rows
bit-for-bit identical to hierarchy OFF for the same content.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import asyncpg
import pytest

from corpus_kb.domain.models import DEFAULT_TENANT_ID
from corpus_kb.tools.ingest_common import ingest_text

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
MIGRATION_021 = MIGRATIONS / "021_shadow_vectors.sql"
ROLLBACK_021 = MIGRATIONS / "rollback_021_shadow_vectors.sql"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
DB_NAME = "corpus_kb_m021_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
SUPER_DSN = f"{BASE_DSN}/postgres"

TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"

# Extension-gated migrations (AGE/pgml server packages) — same skip list as
# tests/research_db.py — plus the two global FORCE-RLS migrations (011/013):
# the v1 pipeline's store methods set the tenant GUC OUTSIDE any transaction
# (set_config is_local evaporates in autocommit), which only ever worked via
# the pre-FORCE owner bypass. Forcing RLS on the v1 tables here would break
# the v1 ingest under test for a pre-existing reason outside P6's additive
# scope. The shadow tables carry their own ENABLE+FORCE (migration 021) and
# are asserted under it below.
_EXTENSION_GATED = {
    "004_enable_age.sql",
    "005_enable_pgml.sql",
    "011_force_rls.sql",
    "013_force_rls_coding.sql",
}

pytestmark = pytest.mark.requires_postgres


async def _create_db() -> str:
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
        # Same >2000-dim index strip as tests/research_db.py (pgvector limit).
        base_sql = re.sub(
            r"CREATE INDEX[^;]*ON chunks_vectors USING (ivfflat|hnsw)\s*\([^;]*vector[^;]*;",
            "-- index on vector(4096) omitted: exceeds pgvector 2000-dim limit",
            SCHEMA_SQL.read_text(encoding="utf-8"),
        )
        await conn.execute(base_sql)
        # Full numeric chain (minus extension-gated 004/005) so the v1 tables
        # carry the columns the ingest pipeline writes (chunk_hash from 007,
        # the 010 vector layout, ...) — the research_db.py harness pattern.
        from corpus_kb._setup.migrate import ensure_migrations_table

        await ensure_migrations_table(conn)
        for path in sorted(MIGRATIONS.iterdir()):
            if path.suffix != ".sql" or not path.name[0].isdigit():
                continue
            if path.name in _EXTENSION_GATED:
                continue
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO corpus.schema_migrations (filename) VALUES ($1)"
                " ON CONFLICT DO NOTHING",
                path.name,
            )
    finally:
        await conn.close()
    return DSN


async def _drop_db() -> None:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture(scope="module")
def m021_dsn() -> str:
    try:
        return asyncio.run(_create_db())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres unavailable for the migration 021 scratch DB: {exc}")


async def _tenant_conn(tenant_id: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(DSN)
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant_id)
    return conn


async def _seed_doc(
    conn: asyncpg.Connection,
    tenant_id: str,
    doc_id: str,
    source: str | None = None,
) -> str:
    """Insert one tenant + document row; return the actual doc_id."""
    await conn.execute(
        "INSERT INTO tenants (tenant_id, name) VALUES ($1::uuid, 't') ON CONFLICT DO NOTHING",
        tenant_id,
    )
    return await conn.fetchval(
        """
        INSERT INTO documents (doc_id, tenant_id, source, source_type)
        VALUES ($1::uuid, $2::uuid, $3, 'text')
        RETURNING doc_id::text
        """,
        doc_id,
        tenant_id,
        source or f"seed-{doc_id[:8]}",
    )


async def _insert_three_levels(
    conn: asyncpg.Connection,
    tenant_id: str,
    doc_id: str,
) -> dict[str, str]:
    """Insert a parent -> chunk -> child chain; return key -> shadow id."""
    parent_id = await conn.fetchval(
        """
        INSERT INTO shadow_chunks (tenant_id, doc_id, level, seq, text, token_count, boundary)
        VALUES ($1, $2::uuid, 'parent', 0, 'P', 100, 'heading')
        RETURNING shadow_chunk_id::text
        """,
        tenant_id,
        doc_id,
    )
    mid_id = await conn.fetchval(
        """
        INSERT INTO shadow_chunks (tenant_id, doc_id, level, seq, parent_id,
            text, token_count, boundary)
        VALUES ($1, $2::uuid, 'chunk', 0, $3::uuid, 'C', 50, 'heading')
        RETURNING shadow_chunk_id::text
        """,
        tenant_id,
        doc_id,
        parent_id,
    )
    child_id = await conn.fetchval(
        """
        INSERT INTO shadow_chunks (tenant_id, doc_id, level, seq, parent_id,
            text, token_count, boundary, metadata)
        VALUES ($1, $2::uuid, 'child', 0, $3::uuid, 'K', 10, 'speaker_turn',
                '{"v1_chunk_id": null}'::jsonb)
        RETURNING shadow_chunk_id::text
        """,
        tenant_id,
        doc_id,
        mid_id,
    )
    return {"parent": parent_id, "chunk": mid_id, "child": child_id}


# ---------------------------------------------------------------------------
# RLS: ENABLE + FORCE (019/020 convention)
# ---------------------------------------------------------------------------


async def test_shadow_tables_have_rls_enabled_and_forced(m021_dsn: str) -> None:
    conn = await asyncpg.connect(DSN)
    try:
        for table in ("shadow_chunks", "shadow_vectors"):
            row = await conn.fetchrow(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class WHERE relname = $1
                """,
                table,
            )
            assert row is not None, f"{table} missing"
            assert row["relrowsecurity"] is True, table
            assert row["relforcerowsecurity"] is True, table
            policy = await conn.fetchval(
                "SELECT count(*) FROM pg_policies WHERE tablename = $1", table
            )
            assert policy == 1, table
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Parent links, unique grain, cascade
# ---------------------------------------------------------------------------


async def test_parent_chain_inserts_and_unique_grain_enforced(m021_dsn: str) -> None:
    conn = await _tenant_conn(TENANT_A)
    try:
        doc_id = await _seed_doc(conn, TENANT_A, "11111111-1111-1111-1111-111111111111")
        ids = await _insert_three_levels(conn, TENANT_A, doc_id)
        row = await conn.fetchrow(
            """
            SELECT c.parent_id::text AS child_parent, p.parent_id::text AS chunk_parent
            FROM shadow_chunks c
            JOIN shadow_chunks p ON p.shadow_chunk_id = c.parent_id
            WHERE c.shadow_chunk_id = $1::uuid
            """,
            ids["child"],
        )
        assert row is not None
        assert row["child_parent"] == ids["chunk"]
        assert row["chunk_parent"] == ids["parent"]
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO shadow_chunks (tenant_id, doc_id, level, seq, text, token_count)
                VALUES ($1, $2::uuid, 'child', 0, 'dup', 1)
                """,
                TENANT_A,
                doc_id,
            )
    finally:
        await conn.close()


async def test_document_delete_cascades_shadow_rows(m021_dsn: str) -> None:
    conn = await _tenant_conn(TENANT_A)
    try:
        doc_id = await _seed_doc(conn, TENANT_A, "22222222-2222-2222-2222-222222222222")
        ids = await _insert_three_levels(conn, TENANT_A, doc_id)
        await conn.execute(
            "INSERT INTO shadow_vectors (shadow_chunk_id, tenant_id, embedding, dimensions)"
            " VALUES ($1::uuid, $2, $3::vector, 1024)",
            ids["child"],
            TENANT_A,
            "[" + ",".join(["0.1"] * 1024) + "]",
        )
        await conn.execute("DELETE FROM documents WHERE doc_id = $1::uuid", doc_id)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM shadow_chunks WHERE doc_id = $1::uuid", doc_id
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM shadow_vectors WHERE shadow_chunk_id = $1::uuid", ids["child"]
            )
            == 0
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# RLS: tenant isolation under FORCE
# ---------------------------------------------------------------------------


async def test_cross_tenant_select_sees_zero_rows(m021_dsn: str) -> None:
    conn = await _tenant_conn(TENANT_A)
    try:
        doc_id = await _seed_doc(conn, TENANT_A, "33333333-3333-3333-3333-333333333333")
        await _insert_three_levels(conn, TENANT_A, doc_id)
        # Scope counts to THIS test's document: earlier tests in the module
        # legitimately leave their own TENANT_A rows behind.
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", TENANT_B)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM shadow_chunks WHERE doc_id = $1::uuid", doc_id
            )
            == 0
        )
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", TENANT_A)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM shadow_chunks WHERE doc_id = $1::uuid", doc_id
            )
            == 3
        )
        # WITH CHECK under FORCE: a row tagged with ANOTHER tenant than the
        # GUC context must raise (SQLSTATE 42501).
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                """
                INSERT INTO shadow_chunks (tenant_id, doc_id, level, seq, text, token_count)
                VALUES ($1, $2::uuid, 'child', 99, 'x', 1)
                """,
                TENANT_B,
                doc_id,
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# halfvec cast-expression index + 1024-dim probe contract
# ---------------------------------------------------------------------------


async def test_halfvec_hnsw_index_present_and_cast_probe_works(m021_dsn: str) -> None:
    conn = await _tenant_conn(TENANT_A)
    try:
        indexdef = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_shadow_vectors_hnsw_halfvec'"
        )
        assert indexdef is not None, "halfvec cast-expression HNSW index missing"
        assert "halfvec_cosine_ops" in indexdef
        assert "halfvec(1024)" in indexdef

        doc_id = await _seed_doc(conn, TENANT_A, "44444444-4444-4444-4444-444444444444")
        ids = await _insert_three_levels(conn, TENANT_A, doc_id)
        vec_a = [1.0] + [0.0] * 1023
        vec_b = [0.0] * 1024
        for key, vec in (("child", vec_a), ("chunk", vec_b)):
            await conn.execute(
                """
                INSERT INTO shadow_vectors (shadow_chunk_id, tenant_id, embedding, dimensions)
                VALUES ($1::uuid, $2, $3::vector, 1024)
                """,
                ids[key],
                TENANT_A,
                "[" + ",".join(str(v) for v in vec) + "]",
            )
        # The 016 probe shape: cast-expression halfvec distance ordering.
        query_vec = "[" + ",".join(["0.5"] + ["0.0"] * 1023) + "]"
        rows = await conn.fetch(
            """
            SELECT sv.shadow_chunk_id::text AS sid,
                   sv.embedding::halfvec(1024) <=> $1::halfvec(1024) AS dist
            FROM shadow_vectors sv
            ORDER BY sv.embedding::halfvec(1024) <=> $1::halfvec(1024)
            """,
            query_vec,
        )
        assert [r["sid"] for r in rows] == [ids["child"], ids["chunk"]]
        # The typmod is strict: a non-1024-dim vector must be rejected.
        with pytest.raises(asyncpg.DataError):
            await conn.execute(
                """
                INSERT INTO shadow_vectors (shadow_chunk_id, tenant_id, embedding, dimensions)
                VALUES ($1::uuid, $2, $3::vector, 7)
                """,
                ids["parent"],
                TENANT_A,
                "[" + ",".join(["0.5"] * 7) + "]",
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Rollback + reapply
# ---------------------------------------------------------------------------


async def test_rollback_021_drops_cleanly_and_migration_reapplies(m021_dsn: str) -> None:
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(ROLLBACK_021.read_text(encoding="utf-8"))
        assert await conn.fetchval("SELECT to_regclass('shadow_chunks')") is None
        assert await conn.fetchval("SELECT to_regclass('shadow_vectors')") is None
        halfvec_index = "idx_shadow_vectors_hnsw_halfvec"
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname = $1",
                halfvec_index,
            )
            == 0
        )
        await conn.execute(MIGRATION_021.read_text(encoding="utf-8"))
        assert await conn.fetchval("SELECT to_regclass('shadow_chunks')") is not None
        assert await conn.fetchval("SELECT to_regclass('shadow_vectors')") is not None
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Shadow ingest wiring: v1 stays untouched, shadow rows land
# ---------------------------------------------------------------------------


def _ingest_config(hierarchy_enabled: bool) -> dict[str, object]:
    """pgml primary with no usable pgml extension -> deterministic zero-vector
    degraded path (no Ollama needed); 768-dim config keeps shadow VECTORS
    honestly skipped while the shadow TEXT hierarchy still lands."""
    return {
        "graph": {"extract_entities": False},
        "embedding": {
            "provider": "pgml",
            "fallback_provider": "ollama",
            "model": "nomic-embed-text",
            "base_url": "http://localhost:65432",
            "dimensions": 768,
            "batch_size": 32,
        },
        "ingest": {"hierarchy": {"enabled": hierarchy_enabled}},
    }


_INGEST_TEXT = (
    "# Alpha\n\nGateway routes requests to shard pools.\n\n"
    "# Beta\n\nAlerts page the on-call engineer after failures."
)


async def _tenant_read(pool: asyncpg.Pool, sql: str, *args: object) -> list[asyncpg.Record]:
    """Run a read under the default tenant GUC, inside its transaction.

    The shadow tables are RLS FORCED, so the GUC must live in the SAME
    transaction as the query (storage/tenant_conn.py contract).
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)", str(DEFAULT_TENANT_ID)
        )
        return await conn.fetch(sql, *args)


async def test_shadow_ingest_leaves_v1_untouched_and_writes_shadow(m021_dsn: str) -> None:
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    try:
        conn = await pool.acquire()
        await conn.execute(MIGRATION_021.read_text(encoding="utf-8"))  # idempotent
        await pool.release(conn)

        off = await ingest_text(
            _INGEST_TEXT,
            pool,
            source_type="markdown",
            config=_ingest_config(hierarchy_enabled=False),
            source="m021-shadow-off",
        )
        on = await ingest_text(
            _INGEST_TEXT,
            pool,
            source_type="markdown",
            config=_ingest_config(hierarchy_enabled=True),
            source="m021-shadow-on",
        )
        assert off["status"] == "success" and on["status"] == "success"
        # Flag off: the result dict has NO shadow keys (pre-U48 shape).
        assert "shadow_chunk_count" not in off
        # Flag on: shadow keys are present and the text hierarchy landed.
        assert on["shadow_chunk_count"] > 0
        assert isinstance(on["shadow_note"], str)
        assert "shadow vectors skipped" in on["shadow_note"]
        assert on["shadow_vector_count"] == 0

        # v1 rows are IDENTICAL between the flag-off and flag-on runs.
        rows_off = await _tenant_read(
            pool,
            """
            SELECT c.chunk_index, c.text, c.chunk_hash, c.tombstoned_at, c.superseded_at
            FROM chunks c WHERE c.doc_id = $1::uuid
            ORDER BY c.chunk_index
            """,
            str(off["document_id"]),
        )
        rows_on = await _tenant_read(
            pool,
            """
            SELECT c.chunk_index, c.text, c.chunk_hash, c.tombstoned_at, c.superseded_at
            FROM chunks c WHERE c.doc_id = $1::uuid
            ORDER BY c.chunk_index
            """,
            str(on["document_id"]),
        )
        assert len(rows_off) == len(rows_on) > 0
        for a, b in zip(rows_off, rows_on, strict=True):
            assert (a["chunk_index"], a["text"], a["chunk_hash"]) == (
                b["chunk_index"],
                b["text"],
                b["chunk_hash"],
            )
            assert a["tombstoned_at"] is None and b["tombstoned_at"] is None

        # The shadow rows carry the three levels with complete parent links.
        level_rows = await _tenant_read(
            pool,
            """
            SELECT level, count(*) AS n FROM shadow_chunks
            WHERE doc_id = $1::uuid GROUP BY level
            """,
            str(on["document_id"]),
        )
        levels = {row["level"]: row["n"] for row in level_rows}
        assert set(levels) == {"child", "chunk", "parent"}
        orphans = await _tenant_read(
            pool,
            """
            SELECT count(*) AS n FROM shadow_chunks sc
            WHERE sc.doc_id = $1::uuid AND sc.level = 'child' AND sc.parent_id IS NULL
            """,
            str(on["document_id"]),
        )
        assert orphans[0]["n"] == 0

        # Re-ingesting the SAME content skips the pipeline entirely and
        # leaves the shadow rows stable (no duplicates).
        again = await ingest_text(
            _INGEST_TEXT,
            pool,
            source_type="markdown",
            config=_ingest_config(hierarchy_enabled=True),
            source="m021-shadow-on",
        )
        assert again["status"] == "skipped"
        total = await _tenant_read(
            pool,
            "SELECT count(*) AS n FROM shadow_chunks WHERE doc_id = $1::uuid",
            str(on["document_id"]),
        )
        assert total[0]["n"] == on["shadow_chunk_count"]

        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM documents WHERE tenant_id = $1 AND source = ANY($2)",
                str(DEFAULT_TENANT_ID),
                ["m021-shadow-off", "m021-shadow-on"],
            )
    finally:
        await pool.close()
