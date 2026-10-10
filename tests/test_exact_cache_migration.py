"""Migration 020 behavioral test: research_exact_cache immutability + RLS.

The offline suite (tests/test_exact_cache.py) proves the key derivation and
the GET/PUT SQL against fake connections; THIS file proves the SQL behaves
on a live server. Mirrors the scratch-DB harness pattern (tests/research_db.py):
a throwaway database created via the superuser postgres:postgres account,
migration 020 applied, behavior asserted, database dropped after — and a
clean skip when no Postgres is reachable.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import asyncpg
import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
MIGRATION_020 = MIGRATIONS / "020_exact_result_cache.sql"
ROLLBACK_020 = MIGRATIONS / "rollback_020_exact_result_cache.sql"

BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
DB_NAME = "corpus_kb_m020_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
SUPER_DSN = f"{BASE_DSN}/postgres"

TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"
CACHE_KEY = "a" * 64  # CHAR(64) primary key

pytestmark = pytest.mark.requires_postgres

_INSERT_SQL = """
INSERT INTO research_exact_cache
    (cache_key, tenant_id, component_hashes, response, model_name, model_digest)
VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)
"""


async def _create_db() -> str:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user')
    finally:
        await admin.close()

    conn = await asyncpg.connect(DSN)
    try:
        # corpus schema is migration 001's job in the full harness; 020 only
        # needs it to host the trigger function.
        await conn.execute("CREATE SCHEMA IF NOT EXISTS corpus")
        await conn.execute(MIGRATION_020.read_text(encoding="utf-8"))
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
def m020_dsn() -> str:
    try:
        return asyncio.run(_create_db())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres unavailable for the migration 020 scratch DB: {exc}")


async def _tenant_conn(tenant_id: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(DSN)
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", tenant_id)
    return conn


def _insert_args(tenant_id: str, cache_key: str = CACHE_KEY) -> list[object]:
    return [
        cache_key,
        tenant_id,
        '{"unit_text_sha256": "%s"}' % ("b" * 64),
        '{"code": "billing"}',
        "qwen3:4b",
        "sha256:abc123",
    ]


async def test_insert_within_tenant_context_succeeds(m020_dsn: str) -> None:
    """RLS WITH CHECK admits a row whose tenant matches the GUC context."""
    conn = await _tenant_conn(TENANT_A)
    try:
        await conn.execute(_INSERT_SQL, *_insert_args(TENANT_A))
        row = await conn.fetchrow(
            "SELECT response, model_name FROM research_exact_cache WHERE cache_key = $1",
            CACHE_KEY,
        )
        assert row is not None
        # asyncpg returns jsonb as text without a codec.
        assert json.loads(str(dict(row)["response"])) == {"code": "billing"}
    finally:
        await conn.close()


async def test_update_and_delete_raise_immutability_trigger(m020_dsn: str) -> None:
    """Cache rows are write-once: UPDATE and DELETE raise through
    corpus.forbid_exact_cache_mutation — invalidation is by key change."""
    key = "b" * 64  # own row: tests share the module-scoped scratch DB
    conn = await _tenant_conn(TENANT_A)
    try:
        await conn.execute(_INSERT_SQL, *_insert_args(TENANT_A, key))
        with pytest.raises(asyncpg.PostgresError, match="immutable"):
            await conn.execute(
                "UPDATE research_exact_cache SET response = $1::jsonb WHERE cache_key = $2",
                '{"code": "other"}',
                key,
            )
        with pytest.raises(asyncpg.PostgresError, match="immutable"):
            await conn.execute("DELETE FROM research_exact_cache WHERE cache_key = $1", key)
    finally:
        await conn.close()


async def test_cross_tenant_select_sees_zero_rows(m020_dsn: str) -> None:
    """FORCE ROW LEVEL SECURITY: tenant B's context cannot read tenant A's
    cached rows even for an identical cache_key."""
    key = "c" * 64  # own row: tests share the module-scoped scratch DB
    conn = await _tenant_conn(TENANT_A)
    try:
        await conn.execute(_INSERT_SQL, *_insert_args(TENANT_A, key))
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", TENANT_B)
        count = await conn.fetchval(
            "SELECT count(*) FROM research_exact_cache WHERE cache_key = $1", key
        )
        assert count == 0
        # The row is visible again in its own tenant on the same connection.
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", TENANT_A)
        count = await conn.fetchval(
            "SELECT count(*) FROM research_exact_cache WHERE cache_key = $1", key
        )
        assert count == 1
    finally:
        await conn.close()


async def test_rollback_020_drops_cleanly_and_migration_reapplies(m020_dsn: str) -> None:
    """rollback_020 drops table + trigger function without error, and the
    migration can be re-applied afterwards (pure derived artifact)."""
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(ROLLBACK_020.read_text(encoding="utf-8"))
        assert await conn.fetchval("SELECT to_regclass('research_exact_cache')") is None
        assert (
            await conn.fetchval("SELECT to_regprocedure('corpus.forbid_exact_cache_mutation()')")
            is None
        )
        # Re-applying recreates an empty cache.
        await conn.execute(MIGRATION_020.read_text(encoding="utf-8"))
        assert await conn.fetchval("SELECT to_regclass('research_exact_cache')") is not None
        assert await conn.fetchval("SELECT count(*) FROM research_exact_cache") == 0
    finally:
        await conn.close()
