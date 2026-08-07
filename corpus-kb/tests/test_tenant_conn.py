"""Tests for the tenant_connection RLS helper.

The bug this exists to prevent: Postgres only honors
SELECT set_config(name, value, true) (is_local) for the lifetime of the
CURRENT transaction. asyncpg auto-commits every unwrapped statement as its
own transaction, so the tenant setting must be set and used inside the
SAME explicit transaction block, or it reverts to '' before the next query.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from src.storage.tenant_conn import tenant_connection

pytestmark = pytest.mark.asyncio

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


async def test_tenant_connection_persists_context_across_two_statements(pg_pool):
    async with tenant_connection(pg_pool, DEFAULT_TENANT_ID) as conn:
        row = await conn.fetchrow(
            "SELECT current_setting('app.current_tenant_id', true) AS tid"
        )
        assert row["tid"] == DEFAULT_TENANT_ID


async def test_tenant_connection_accepts_uuid_object(pg_pool):
    tenant_id = UUID(DEFAULT_TENANT_ID)
    async with tenant_connection(pg_pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT current_setting('app.current_tenant_id', true) AS tid"
        )
        assert row["tid"] == str(tenant_id)
