"""Postgres leg of the per-project raw-table factory (Track C1).

Runs against the fresh, fully migrated research database (``pool``), so
the registry migration 043 and the 022-convention RLS policy on the
runtime table are both proven on the engine that FORCEs row security.
Auto-skips where no Postgres service exists (the ubuntu CI leg has one).
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.storage.project_tables import (
    create_project_table,
    drop_project_table,
    list_project_tables,
    store_raw_content,
)
from corpus_kb.tools.project_tools import project_drop, project_list, project_provision

pytestmark = pytest.mark.requires_postgres

TENANT = "00000000-0000-0000-0000-000000000001"
PROJECT = "00000000-0000-0000-0000-000000000002"


async def _policy_names(pool: Any, table: str) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT policyname FROM pg_policies WHERE tablename = $1 ORDER BY policyname", table
        )
    return [str(row["policyname"]) for row in rows]


async def test_provision_creates_the_table_with_tenant_rls(pool: Any) -> None:
    out = await create_project_table(pool, TENANT, PROJECT, "pg-notes")
    try:
        assert out["backend"] == "postgres"
        assert out["table"] == "raw_pg_notes"
        assert await _policy_names(pool, "raw_pg_notes") == ["raw_table_tenant_isolation"]
        tables = await list_project_tables(pool, TENANT)
        assert [t["table"] for t in tables] == ["raw_pg_notes"]
        assert tables[0]["row_count"] == 0

        # Idempotent re-provision answers the same registry row.
        again = await create_project_table(pool, TENANT, PROJECT, "pg-notes")
        assert again["already_provisioned"] is True
        assert again["registry_id"] == out["registry_id"]
    finally:
        await drop_project_table(pool, TENANT, "pg-notes")


async def test_raw_rows_are_tenant_isolated_and_content_addressed(pool: Any) -> None:
    other = "00000000-0000-0000-0000-000000000003"
    await create_project_table(pool, TENANT, PROJECT, "pg-raw")
    try:
        digest = await store_raw_content(
            pool,
            TENANT,
            PROJECT,
            "pg-raw",
            source_path="a.txt",
            raw_bytes=None,
            raw_text="hello",
            content_type="text/plain",
        )
        assert len(digest) == 64
        await store_raw_content(
            pool,
            TENANT,
            PROJECT,
            "pg-raw",
            source_path="a.txt",
            raw_bytes=None,
            raw_text="hello",
            content_type="text/plain",
        )
        mine = await list_project_tables(pool, TENANT)
        assert mine[0]["row_count"] == 1
        # The other tenant sees no registry row and (through RLS) no raw row.
        assert await list_project_tables(pool, other) == []
    finally:
        out = await drop_project_table(pool, TENANT, "pg-raw")
        assert out["dropped_rows"] == 1


async def test_shared_handlers_round_trip_on_postgres(pool: Any) -> None:
    provision = await project_provision(pool, slug="pg-ops", tenant_id=TENANT)
    assert provision["status"] == "success"
    assert provision["backend"] == "postgres"
    listed = await project_list(pool, tenant_id=TENANT)
    assert listed["count"] >= 1
    dropped = await project_drop(pool, slug="pg-ops", tenant_id=TENANT)
    assert dropped == {"status": "success", "table": "raw_pg_ops", "dropped_rows": 0}


async def test_doctor_probe_pg_branch_is_warning_free(pool: Any) -> None:
    """The PG probe awaits every read: no leaked-coroutine RuntimeWarnings."""
    import warnings

    from corpus_kb._setup.doctor_project_tables import _read_state, _tables_present

    await project_provision(pool, slug="pg-doctor", tenant_id=TENANT)
    try:
        async with pool.acquire() as conn, conn.transaction():
            # The registry FORCEs RLS: set the tenant GUC like the probe does.
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", TENANT)
            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                assert await _tables_present(conn, is_postgres=True) is True
                registered, orphans, missing = await _read_state(conn, is_postgres=True)
        assert "raw_pg_doctor" in registered
        assert orphans == [] and missing == []
    finally:
        await project_drop(pool, slug="pg-doctor", tenant_id=TENANT)
