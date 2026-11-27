"""Migration 051 live verification: the pre-W3 source_type backfill (#591).

Runs against a THROWAWAY database (drop/create per module): stages the
packaged migrations through the shared ``apply_schema`` bootstrap (base
schema + every numbered file except 051 itself), seeds pre-W3 rows, then
drives the real 051 file through the packaged runner:

* legacy ``web`` rows re-stamp to the W3 canonical ``external-site``;
* canonical and generic rows (``external-site``, ``text``) stay untouched;
* the row count moves one for one: every row counted ``web`` before the
  run is counted ``external-site`` after it (row-count parity);
* another tenant's rows backfill the same way (RLS never hides a row);
* the step is idempotent (a second run matches nothing);
* the rollback twin runs clean and changes nothing.

The seed inserts run with the tenant GUC set (documents is FORCE RLS,
011); the migration itself runs as the bare owner, so it also proves the
FORCE-state capture/restore inside the file works. Cluster-truth counts
read through the superuser connection (BYPASSRLS). Every statement in
this file either is a fixed literal with bound values or passes file
PATHS to the packaged runner; no SQL text is composed here.

Credentials follow the storage portability program rule 20: admin and app
DSNs come from tests/research_db.py (POSTGRES_* env; the CI service image
makes the bootstrap role itself the superuser). No role names or passwords
are hardcoded here. Auto-skips when no Postgres admin connection resolves,
never failing on the environment (the test_migration_044 precedent).
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import research_db

pytestmark = pytest.mark.requires_postgres

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"
MIGRATION_051 = MIGRATIONS / "051_source_type_backfill.sql"
ROLLBACK_051 = MIGRATIONS / "rollback_051_source_type_backfill.sql"

# One fixed literal name for this module's scratch database, mirrored
# inside the two DDL literals below. Distinct migration tests use distinct
# fixed names, so they never collide; the DDL stays constant text, with
# every mutable value bound at the call sites.
DB_NAME = "corpus_kb_mig051_test"
DSN = f"{research_db.BASE_DSN}/{DB_NAME}"

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"


async def _drop_db() -> None:
    """Best-effort drop through the admin DSN; never masks a skip."""
    admin = await asyncpg.connect(research_db.super_dsn_for("postgres"))
    try:
        await admin.execute("DROP DATABASE IF EXISTS corpus_kb_mig051_test WITH (FORCE)")
    finally:
        await admin.close()


async def _bootstrap_pre_051(tmp_path: Path) -> None:
    """Create the scratch DB, extensions, base schema, and pre-051 migrations.

    The staged directory holds copies of every numbered migration except
    051, so the shared bootstrap applies the pre-051 world and the test
    drives the real 051 file afterward.
    """
    await _drop_db()
    admin = await asyncpg.connect(research_db.super_dsn_for("postgres"))
    try:
        await admin.execute("CREATE DATABASE corpus_kb_mig051_test OWNER corpus_user")
    finally:
        await admin.close()

    superconn = await asyncpg.connect(research_db.super_dsn_for(DB_NAME))
    try:
        await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    finally:
        await superconn.close()

    staged = tmp_path / "pre051"
    staged.mkdir()
    for path in sorted(MIGRATIONS.iterdir()):
        if path.suffix == ".sql" and path.name[0].isdigit() and path.name != MIGRATION_051.name:
            shutil.copy(path, staged / path.name)

    from corpus_kb._setup.schema_apply import apply_schema

    await apply_schema(DSN, migrations_dir=staged)


async def _seed(conn: asyncpg.Connection) -> dict[str, int]:
    """Per tenant: two legacy web rows, one canonical, one generic text row.

    Returns the per-type seeded counts summed across both tenants, the
    numbers the row-count parity assertions compare against.
    """
    counts = {"web": 0, "external-site": 0, "text": 0}
    for tenant in (TENANT, OTHER_TENANT):
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant)
            for source, source_type in (
                (f"/corpus/{tenant}-web-a.md", "web"),
                (f"/corpus/{tenant}-web-b.md", "web"),
                (f"/corpus/{tenant}-canonical.md", "external-site"),
                (f"/corpus/{tenant}-text.md", "text"),
            ):
                await conn.execute(
                    """
                    INSERT INTO documents (doc_id, tenant_id, source, source_type)
                    VALUES ($1, $2, $3, $4)
                    """,
                    str(uuid4()),
                    tenant,
                    source,
                    source_type,
                )
                counts[source_type] += 1
    return counts


async def _counts(superconn: asyncpg.Connection) -> dict[str, int]:
    """Cluster-truth source_type counts (BYPASSRLS: RLS never hides a row)."""
    rows = await superconn.fetch(
        "SELECT source_type, count(*) AS n FROM documents"
        " WHERE source LIKE $1 GROUP BY source_type",
        "/corpus/%",
    )
    return {str(r["source_type"]): int(r["n"]) for r in rows}


async def _documents_force(conn: asyncpg.Connection) -> bool:
    flag = await conn.fetchval(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'documents'::regclass"
    )
    return bool(flag)


async def test_source_type_backfill_moves_counts_one_for_one(tmp_path: Path) -> None:
    try:
        await _bootstrap_pre_051(tmp_path)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres unavailable for the migration 051 scratch DB: {exc}")

    from corpus_kb._setup.migrate_postgres import _apply_pending
    from corpus_kb._setup.store_width import configured_embedding_dims

    conn = await asyncpg.connect(DSN)
    superconn = await asyncpg.connect(research_db.super_dsn_for(DB_NAME))
    try:
        seeded = await _seed(conn)
        # The bootstrap applied 011, so documents starts FORCEd; the
        # backfill must leave the flag EXACTLY as it found it.
        assert await _documents_force(conn) is True

        dims = configured_embedding_dims()
        await _apply_pending(conn, [MIGRATION_051], set(), dims)

        after = await _counts(superconn)
        assert after.get("web", 0) == 0, "every legacy web row must re-stamp"
        assert after.get("external-site", 0) == (seeded["web"] + seeded["external-site"]), (
            "row-count parity: each seeded web row joins the canonical bucket"
        )
        assert after.get("text", 0) == seeded["text"], "generic rows stay untouched"
        assert await _documents_force(conn) is True, "the backfill restores the FORCE state"

        # Idempotent: clear the applied record, then re-apply the real file
        # through the same runner path.
        await conn.execute(
            "DELETE FROM corpus.schema_migrations WHERE filename = $1",
            MIGRATION_051.name,
        )
        await _apply_pending(conn, [MIGRATION_051], set(), dims)
        assert await _counts(superconn) == after, "the backfill must be idempotent"

        # The rollback twin runs clean and changes nothing (data repair).
        await _apply_pending(conn, [ROLLBACK_051], set(), dims)
        assert await _counts(superconn) == after, "the rollback is a no-op"
    finally:
        await conn.close()
        await superconn.close()
        with contextlib.suppress(OSError, asyncpg.PostgresError):
            await _drop_db()
