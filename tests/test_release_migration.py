"""Migration 019 (release tables) — live-Postgres behavioral proofs.

Follows the tests/research_db.py throwaway-database pattern: a dedicated
scratch database (superuser creates it, corpus_user owns everything in it),
migration 019 applied, behavior asserted, rollback applied, drop. Skips when
Postgres or the superuser account is unavailable (CI without Postgres).

Proven: RLS ENABLE+FORCE + tenant policy on all four tables; the released-row
immutability trigger (content edits and deletes blocked, supersede/retire
transitions allowed); child-table freeze once the parent releases; the
tenant-scoped FK guard; the partial unique index (one released row per
(tenant, codebook, version)); rollback cleanliness; apply idempotency.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from corpus_kb.research.release.manifest import canonical_json_bytes

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "src" / "corpus_kb" / "migrations" / "019_codebook_releases.sql"
ROLLBACK = REPO / "src" / "corpus_kb" / "migrations" / "rollback_019_codebook_releases.sql"
BASE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432"
SUPER_DSN = f"{BASE_DSN}/postgres"
DB_NAME = "corpus_kb_m019_test"
DSN = f"{BASE_DSN}/{DB_NAME}"
TABLES = (
    "codebook_releases",
    "release_gate_results",
    "release_waivers",
    "audit_partitions",
)
TENANT = "00000000-0000-0000-0000-000000000002"
OTHER_TENANT = "00000000-0000-0000-0000-000000000003"

pytestmark = pytest.mark.requires_postgres


async def _superuser_available() -> bool:
    try:
        conn = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres", timeout=3)
    except (OSError, asyncpg.PostgresError):
        return False
    await conn.close()
    return True


async def _setup_db() -> None:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user')
    finally:
        await admin.close()
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(MIGRATION.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def _teardown_db() -> None:
    admin = await asyncpg.connect(SUPER_DSN, user="postgres", password="postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    finally:
        await admin.close()


async def _tenant_tx(sql: str, *args: object, tenant: str = TENANT) -> list[asyncpg.Record]:
    """Run one statement under a tenant GUC (RLS FORCE applies to the owner)."""
    conn = await asyncpg.connect(DSN)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant)
            return list(await conn.fetch(sql, *args))
    finally:
        await conn.close()


async def _tenant_exec(sql: str, *args: object, tenant: str = TENANT) -> str:
    conn = await asyncpg.connect(DSN)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant)
            return str(await conn.execute(sql, *args))
    finally:
        await conn.close()


def _insert_release_sql() -> str:
    return """
        INSERT INTO codebook_releases (release_id, tenant_id, codebook_id,
            codebook_version, codebook_version_sha256, state, profile,
            manifest_json, manifest_sha256, creator)
        VALUES ($1, $2, $3, $4, $5, 'draft_candidate', 'team-codebook',
                $6::jsonb, $7, 'alice')
        """


async def _insert_draft(release_id: object, tenant: str = TENANT) -> None:
    await _tenant_exec(
        _insert_release_sql(),
        release_id,
        tenant,
        uuid4(),
        uuid4(),
        "a" * 64,
        canonical_json_bytes({"k": 1}).decode("utf-8"),
        "h" * 64,
        tenant=tenant,
    )


@pytest.mark.asyncio
async def test_migration_019_tables_rls_and_immutability():
    if not await _superuser_available():
        pytest.skip("Postgres superuser unavailable")
    await _setup_db()
    try:
        await _assert_rls_configuration()
        await _assert_released_row_immutability()
        await _assert_child_freeze_and_tenant_fk()
        await _assert_one_released_row_per_version()
        await _assert_audit_partition_disjointness()
        await _assert_rollback_clean_and_idempotent()
    finally:
        await _teardown_db()


async def _assert_rls_configuration() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   COUNT(p.polname) AS policies
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_policy p ON p.polrelid = c.oid
            WHERE n.nspname = 'public' AND c.relname = ANY($1)
            GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
            """,
            list(TABLES),
        )
        assert len(rows) == 4
        for row in rows:
            assert row["relrowsecurity"] and row["relforcerowsecurity"]
            assert row["policies"] == 1
    finally:
        await conn.close()


async def _assert_released_row_immutability() -> None:
    release_id = uuid4()
    await _insert_draft(release_id)
    # A draft edits freely (gate attach is the draft's whole point).
    count = await _tenant_exec(
        "UPDATE codebook_releases SET manifest_sha256 = $2 WHERE release_id = $1",
        release_id,
        "i" * 64,
    )
    assert count.startswith("UPDATE 1")
    released = await _tenant_exec(
        "UPDATE codebook_releases SET state = 'released', approver = $2, "
        "released_at = NOW() WHERE release_id = $1 AND state = 'draft_candidate'",
        release_id,
        "dana",
    )
    assert released.startswith("UPDATE 1")
    with pytest.raises(asyncpg.PostgresError, match="immutable"):
        await _tenant_exec(
            "UPDATE codebook_releases SET manifest_json = $2::jsonb WHERE release_id = $1",
            release_id,
            '{"tampered": true}',
        )
    with pytest.raises(asyncpg.PostgresError, match="immutable"):
        await _tenant_exec("DELETE FROM codebook_releases WHERE release_id = $1", release_id)
    # The guarded transitions survive: released -> retired is legal.
    retired = await _tenant_exec(
        "UPDATE codebook_releases SET state = 'retired', retired_at = NOW() WHERE release_id = $1",
        release_id,
    )
    assert retired.startswith("UPDATE 1")
    await _tenant_exec("DELETE FROM codebook_releases WHERE release_id = $1", release_id)


async def _assert_child_freeze_and_tenant_fk() -> None:
    release_id = uuid4()
    await _insert_draft(release_id)
    gate_sql = """
        INSERT INTO release_gate_results (tenant_id, release_id, gate_id, status)
        VALUES ($1, $2, 'machine_profile', 'not_evaluable')
        """
    await _tenant_exec(gate_sql, TENANT, release_id)
    # Foreign-tenant child insert is rejected by the tenant-scoped FK guard.
    with pytest.raises(asyncpg.PostgresError, match="not found for tenant"):
        await _tenant_exec(gate_sql, TENANT, release_id, tenant=OTHER_TENANT)
    released = await _tenant_exec(
        "UPDATE codebook_releases SET state = 'released', approver = 'dana', "
        "released_at = NOW() WHERE release_id = $1",
        release_id,
    )
    assert released.startswith("UPDATE 1")
    with pytest.raises(asyncpg.PostgresError, match="frozen"):
        await _tenant_exec(
            "UPDATE release_gate_results SET status = 'pass' WHERE release_id = $1",
            release_id,
        )
    with pytest.raises(asyncpg.PostgresError, match="frozen"):
        await _tenant_exec(
            "INSERT INTO release_waivers (tenant_id, release_id, gate_id, "
            "justification, approver) VALUES ($1, $2, 'g', 'why', 'who')",
            TENANT,
            release_id,
        )
    # Empty justification/approver are schema-rejected.
    draft2 = uuid4()
    await _insert_draft(draft2)
    with pytest.raises(asyncpg.PostgresError):
        await _tenant_exec(
            "INSERT INTO release_waivers (tenant_id, release_id, gate_id, "
            "justification, approver) VALUES ($1, $2, 'g', '   ', 'who')",
            TENANT,
            draft2,
        )


async def _assert_one_released_row_per_version() -> None:
    codebook_id, version_id = uuid4(), uuid4()
    first, second, other_version = uuid4(), uuid4(), uuid4()
    insert = _insert_release_sql()
    for rid in (first, second, other_version):
        await _tenant_exec(
            insert,
            rid,
            TENANT,
            codebook_id,
            version_id if rid != other_version else uuid4(),
            "a" * 64,
            "{}",
            "h" * 64,
        )
    # First release of the version succeeds...
    released = await _tenant_exec(
        "UPDATE codebook_releases SET state = 'released', approver = 'x', "
        "released_at = NOW() WHERE release_id = $1",
        first,
    )
    assert released.startswith("UPDATE 1")
    # ...a SECOND concurrent release of the SAME version is the DB-level race
    # guard: the partial unique index rejects it.
    with pytest.raises(asyncpg.PostgresError, match="uq_codebook_releases_released"):
        await _tenant_exec(
            "UPDATE codebook_releases SET state = 'released', approver = 'x', "
            "released_at = NOW() WHERE release_id = $1",
            second,
        )
    # A different version under the same codebook_id releases fine.
    third = await _tenant_exec(
        "UPDATE codebook_releases SET state = 'released', approver = 'x', "
        "released_at = NOW() WHERE release_id = $1",
        other_version,
    )
    assert third.startswith("UPDATE 1")
    count = await _tenant_tx(
        """
        SELECT count(*) FROM codebook_releases
        WHERE tenant_id = $1 AND codebook_id = $2 AND state = 'released'
        """,
        TENANT,
        codebook_id,
    )
    assert count[0]["count"] == 2


async def _assert_audit_partition_disjointness() -> None:
    unit = 4242
    await _tenant_exec(
        """
        INSERT INTO audit_partitions (tenant_id, scope, unit_id, partition,
            stratum, inclusion_probability, seed)
        VALUES ($1, 'scope-a', $2, 'audit', 'cost|high', 0.25, 42)
        """,
        TENANT,
        unit,
    )
    with pytest.raises(asyncpg.PostgresError, match="duplicate key"):
        await _tenant_exec(
            """
            INSERT INTO audit_partitions (tenant_id, scope, unit_id, partition)
            VALUES ($1, 'scope-a', $2, 'test')
            """,
            TENANT,
            unit,
        )
    # A different partition value outside the CHECK is rejected.
    with pytest.raises(asyncpg.PostgresError, match="partition"):
        await _tenant_exec(
            """
            INSERT INTO audit_partitions (tenant_id, scope, unit_id, partition)
            VALUES ($1, 'scope-b', $2, 'train')
            """,
            TENANT,
            unit,
        )


async def _assert_rollback_clean_and_idempotent() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(ROLLBACK.read_text(encoding="utf-8"))
        leftovers = await conn.fetch(
            """
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY($1)
              AND c.relkind IN ('r', 'p')
            """,
            list(TABLES),
        )
        assert leftovers == []
        # Idempotent re-apply after rollback.
        await conn.execute(MIGRATION.read_text(encoding="utf-8"))
        again = await conn.fetch(
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = ANY($1)",
            list(TABLES),
        )
        assert len(again) == 4
    finally:
        await conn.close()
