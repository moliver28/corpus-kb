"""Gateway resolution + replayable write units for the projections.

The projection write side (checkpoints, DLQ, materialized tables) goes
through one :class:`SqlGateway` (program doc §3.1). Postgres keeps today's
one-transaction-per-statement semantics via :class:`PostgresGateway`; DuckDB
gets conflict replay, because a write that meets a rival row version raises
DuckDB's ``TransactionException`` ("Conflict on update!") and the whole unit
must be retried (W0 storage-ops spike 3).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar, runtime_checkable
from uuid import UUID

import asyncpg

from corpus_kb.errors import ConfigError
from corpus_kb.storage.gateway import PostgresGateway, SqlGateway

T = TypeVar("T")


@runtime_checkable
class ReplayableGateway(Protocol):
    """Structural view of a gateway whose write units replay on conflict.

    Only :class:`DuckDbGateway` carries ``run_transaction`` today; the plain
    ``transaction()`` scope stays the fallback, which keeps Postgres
    byte-identical (no replay loop existed there before this program).
    """

    async def run_transaction(
        self, tenant_id: str, body: Callable[[SqlGateway], Awaitable[T]]
    ) -> T: ...


def resolve_gateway(pool: asyncpg.Pool | None, gateway: SqlGateway | None) -> SqlGateway:
    """The injected gateway, else the Postgres driver over the pool."""
    if gateway is not None:
        return gateway
    if pool is not None:
        return PostgresGateway(pool)
    raise ConfigError("Projections need a SqlGateway or an asyncpg pool.")


async def run_write_unit(
    gateway: SqlGateway, tenant_id: str | UUID, body: Callable[[SqlGateway], Awaitable[T]]
) -> T:
    """Run one atomic write; replay the whole unit when the driver conflicts."""
    if isinstance(gateway, ReplayableGateway):
        return await gateway.run_transaction(str(tenant_id), body)
    async with gateway.transaction(str(tenant_id)) as tx:
        return await body(tx)
