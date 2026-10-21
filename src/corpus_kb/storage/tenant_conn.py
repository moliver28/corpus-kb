"""Shared tenant-scoped connection helper (Postgres RLS; DuckDB scope).

Every table has an RLS policy that checks
current_setting('app.current_tenant_id', true)::uuid = tenant_id.
Postgres only honors SELECT set_config(name, value, true) (is_local) for
the lifetime of the CURRENT transaction — asyncpg auto-commits every
unwrapped statement as its own transaction, so the tenant setting must be
set and used inside the SAME explicit transaction block.

A :class:`~corpus_kb.storage.gateway.SqlGateway` (the DuckDB file driver,
or an injected Postgres gateway) carries the tenant scope itself:
``transaction(tenant_id)`` sets the same GUC inside a held Postgres
transaction, and DuckDB has no RLS (the WHERE tenant_id predicates stay
the filter there). Both branches yield the same fetch/fetchrow/fetchval/
execute surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

import asyncpg

from corpus_kb.storage.gateway import SqlGateway, is_gateway


@asynccontextmanager
async def tenant_connection(
    pool: asyncpg.Pool | SqlGateway, tenant_id: UUID | str
) -> AsyncIterator[asyncpg.Connection | SqlGateway]:
    """Acquire a pooled connection with RLS tenant context set for one transaction.

    A SqlGateway passes through its own tenant-scoped transaction scope.

    Usage:
        async with tenant_connection(pool, tenant_id) as conn:
            await conn.fetch("SELECT * FROM documents")
    """
    if is_gateway(pool):
        gateway = cast(SqlGateway, pool)
        async with gateway.transaction(str(tenant_id)) as scoped:
            yield scoped
        return
    acquired_pool = cast("asyncpg.Pool", pool)
    async with acquired_pool.acquire() as acquired, acquired.transaction():
        conn = cast("asyncpg.Connection", acquired)
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)",
            str(tenant_id),
        )
        yield conn
