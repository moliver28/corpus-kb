"""Migration 044 live verification: the ingest-ledger doc re-point (#363).

Runs against a THROWAWAY database (drop/create per module): applies
storage/schema.sql + migrations 001+ (skipping the extension-gated 004/005
AGE/pgml pair and 044 itself, per the test_migration_027 precedent), seeds
the corrupted state the pre-fix re-ingest left behind, then drives the
repair:

* a ledger row pointing at a phantom doc id follows the documents row for
  the same (tenant_id, source);
* a healthy ledger row (ids already agree) and another tenant's row stay
  untouched;
* the step is idempotent (a second run matches nothing);
* the rollback is a documented no-op (the repair is data, not shape).

The seed inserts run with the tenant GUC set (documents is FORCE RLS,
011); the migration itself runs as the bare owner, so it also proves the
FORCE-state restore inside the file works.

Credentials follow the storage portability program rule 20: admin and app
DSNs come from tests/research_db.py (POSTGRES_* env; the CI service image
makes the bootstrap role itself the superuser). No role names or passwords
are hardcoded here. Auto-skips when no Postgres admin connection resolves,
never failing on the environment (the test_migration_027 precedent).
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import research_db

pytestmark = pytest.mark.requires_postgres

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"
MIGRATION_044 = MIGRATIONS / "044_ingest_ledger_doc_repair.sql"
ROLLBACK_044 = MIGRATIONS / "rollback_044_ingest_ledger_doc_repair.sql"

# Env-derived seams (tests/research_db.py): the app role owns the scratch
# database; the admin role creates and drops it.
DB_NAME = f"corpus_kb_mig044_{os.getpid() % 100000}_test"
DSN = f"{research_db.BASE_DSN}/{DB_NAME}"

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"

_EXTENSION_GATED = {"004_enable_age.sql", "005_enable_pgml.sql"}
_SKIP_AT_BOOTSTRAP = _EXTENSION_GATED | {"044_ingest_ledger_doc_repair.sql"}


async def _drop_db() -> None:
    """Best-effort drop through the admin DSN; never masks a skip."""
    admin = await asyncpg.connect(research_db.super_dsn_for("postgres"))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    finally:
        await admin.close()


async def _bootstrap() -> None:
    await _drop_db()
    admin = await asyncpg.connect(research_db.super_dsn_for("postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{DB_NAME}" OWNER corpus_user')
    finally:
        await admin.close()

    superconn = await asyncpg.connect(research_db.super_dsn_for(DB_NAME))
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
    finally:
        await conn.close()


async def _seed(conn: asyncpg.Connection) -> dict[str, str]:
    """One healthy pair, one poisoned pair, one other-tenant pair."""
    ids = {
        "real_doc": str(uuid4()),
        "phantom_doc": str(uuid4()),
        "other_real_doc": str(uuid4()),
        "other_phantom_doc": str(uuid4()),
    }
    for tenant, real, phantom, source in (
        (TENANT, ids["real_doc"], ids["phantom_doc"], "/corpus/interview.md"),
        (OTHER_TENANT, ids["other_real_doc"], ids["other_phantom_doc"], "/corpus/other.md"),
    ):
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant)
            await conn.execute(
                """
                INSERT INTO documents (doc_id, tenant_id, source, source_type)
                VALUES ($1, $2, $3, 'transcript')
                """,
                real,
                tenant,
                source,
            )
            await conn.execute(
                """
                INSERT INTO ingested_files
                    (tenant_id, path, file_sha256, source_type, doc_id)
                VALUES ($1, $2, $3, 'transcript', $4)
                """,
                tenant,
                source,
                "a" * 64,
                phantom,
            )
    return ids


async def _ledger_doc_ids(superconn: asyncpg.Connection) -> dict[str, str]:
    """Ledger reads go through BYPASSRLS: the repair asserts cluster truth."""
    rows = await superconn.fetch("SELECT path, doc_id FROM ingested_files")
    return {str(r["path"]): str(r["doc_id"]) for r in rows}


async def _force_flags(conn: asyncpg.Connection) -> dict[str, bool]:
    rows = await conn.fetch(
        "SELECT relname, relforcerowsecurity FROM pg_class "
        "WHERE oid IN ('documents'::regclass, 'ingested_files'::regclass)"
    )
    return {str(r["relname"]): bool(r["relforcerowsecurity"]) for r in rows}


async def test_ledger_repair_repoints_and_is_idempotent() -> None:
    try:
        await _bootstrap()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres unavailable for the migration 044 scratch DB: {exc}")

    conn = await asyncpg.connect(DSN)
    superconn = await asyncpg.connect(research_db.super_dsn_for(DB_NAME))
    try:
        ids = await _seed(conn)
        before = await _ledger_doc_ids(superconn)
        assert before["/corpus/interview.md"] == ids["phantom_doc"]
        # This bootstrap applied 011/016, so both tables start FORCEd; the
        # repair must leave the flag EXACTLY as it found it.
        flags_before = await _force_flags(conn)
        assert flags_before == {"documents": True, "ingested_files": True}

        repair = MIGRATION_044.read_text(encoding="utf-8")
        await conn.execute(repair)
        after = await _ledger_doc_ids(superconn)
        assert after["/corpus/interview.md"] == ids["real_doc"], (
            "the poisoned ledger row must follow the documents row"
        )
        assert after["/corpus/other.md"] == ids["other_real_doc"], (
            "the other tenant's poisoned row must follow its own document"
        )
        assert await _force_flags(conn) == flags_before, (
            "the repair must restore each table's FORCE state"
        )

        await conn.execute(repair)
        again = await _ledger_doc_ids(superconn)
        assert again == after, "the repair must be idempotent"

        await conn.execute(ROLLBACK_044.read_text(encoding="utf-8"))
        assert await _ledger_doc_ids(superconn) == after, "the rollback is a no-op"
    finally:
        await conn.close()
        await superconn.close()
        with contextlib.suppress(OSError, asyncpg.PostgresError):
            await _drop_db()
