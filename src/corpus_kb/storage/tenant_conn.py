"""Shared tenant-scoped connection helper for Postgres RLS.

Every table has an RLS policy that checks
current_setting('app.current_tenant_id', true)::uuid = tenant_id.
Postgres only honors SELECT set_config(name, value, true) (is_local) for
the lifetime of the CURRENT transaction — asyncpg auto-commits every
unwrapped statement as its own transaction, so the tenant setting must be
set and used inside the SAME explicit transaction block.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg


@asynccontextmanager
async def tenant_connection(
    pool: asyncpg.Pool, tenant_id: UUID | str
) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a pooled connection with RLS tenant context set for one transaction.

    Usage:
        async with tenant_connection(pool, tenant_id) as conn:
            await conn.fetch("SELECT * FROM documents")
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)",
            str(tenant_id),
        )
        yield conn
