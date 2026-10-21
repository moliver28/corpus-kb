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

    ``storage.tenant_conn.tenant_connection`` is THE seam: this wrapper
    only retypes the union for the coding domain, so the two arms can
    never drift.
    """
    async with tenant_connection(store, tenant_id) as conn:
        yield conn
