"""The coding domain's one store seam (storage portability program, §8).

Every coding store module takes one ``store``: an asyncpg pool (Postgres
path — byte-identical SQL through :func:`tenant_connection`) or a
:class:`~corpus_kb.storage.gateway.SqlGateway` (portable path — the
gateway's ``transaction(tenant_id)`` scope owns the tenant context, since
DuckDB has no row-level security). :func:`tenant_scope` yields the matching
connection view, so a module branches only where the SQL TEXT must differ
(vector casts, the gateway's chunk_signals no-delete rule), never on how
the store is reached.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

import asyncpg

from corpus_kb.storage.gateway import SqlGateway, is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection

__all__ = ["CodingStore", "ScopedConn", "SqlGateway", "is_gateway", "tenant_scope"]

#: The one store type every coding store module accepts.
CodingStore = asyncpg.Pool | SqlGateway

#: What :func:`tenant_scope` yields: a held Postgres connection or a
#: transaction-scoped gateway view. Both speak fetch/fetchrow/fetchval/execute.
ScopedConn = asyncpg.Connection | SqlGateway


@asynccontextmanager
async def tenant_scope(store: CodingStore, tenant_id: UUID | str) -> AsyncIterator[ScopedConn]:
    """One tenant-scoped unit over either store kind.

    Postgres keeps today's shape exactly: a held pooled connection with the
    RLS ``app.current_tenant_id`` GUC set inside one transaction. A gateway
    yields its own transaction scope, which carries the same role.
    """
    if is_gateway(store):
        gateway = cast("SqlGateway", store)
        async with gateway.transaction(str(tenant_id)) as scoped:
            yield scoped
        return
    pool = cast("asyncpg.Pool", store)
    async with tenant_connection(pool, tenant_id) as conn:
        yield conn
