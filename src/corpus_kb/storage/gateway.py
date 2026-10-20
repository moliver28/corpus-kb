"""The one SQL seam (storage portability program, program doc §3.1).

One :class:`SqlGateway` protocol, two drivers: ``PostgresGateway`` (thin
asyncpg wrapper; SQL text and status tags byte-identical to today) and
``DuckDbGateway`` (``duckdb_gateway.py`` — one connection, thread-locked
calls, transaction scopes that never interleave). Import drivers from THIS
module; it re-exports the DuckDB side so the seam keeps a single import
point.

``execute()`` returns PG-format status tags ("INSERT 0 1", "UPDATE 3");
``projections/research/_common.py`` parses these strings verbatim.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, cast, runtime_checkable

import asyncpg

from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import (
    APPEND_ONLY_TABLES,
    Row,
    duckdb_conn_string_to_path,
    duckdb_path_to_conn_string,
    map_connect_error,
)

__all__ = [
    "APPEND_ONLY_TABLES",
    "DuckDbGateway",
    "PostgresGateway",
    "Row",
    "SqlGateway",
    "duckdb_conn_string_to_path",
    "duckdb_path_to_conn_string",
    "map_connect_error",
]


@runtime_checkable
class SqlGateway(Protocol):
    """The storage seam: every SQL-speaking module takes one of these."""

    async def fetch(self, sql: str, *args: Any) -> list[Row]: ...

    async def fetchrow(self, sql: str, *args: Any) -> Row | None: ...

    async def fetchval(self, sql: str, *args: Any) -> Any: ...

    async def execute(self, sql: str, *args: Any) -> str: ...

    def transaction(self, tenant_id: str) -> AbstractAsyncContextManager[SqlGateway]: ...


class PostgresGateway:
    """Thin asyncpg wrapper. SQL text and status tags stay byte-identical."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch(self, sql: str, *args: Any) -> list[Row]:
        async with self._pool.acquire() as conn:
            return list(await conn.fetch(sql, *args))

    async def fetchrow(self, sql: str, *args: Any) -> Row | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    @asynccontextmanager
    async def transaction(self, tenant_id: str) -> AsyncIterator[_PgConnGateway]:
        """Held connection + tenant GUC, so RLS stays defense-in-depth."""
        async with self._pool.acquire() as conn, conn.transaction():
            held = cast("asyncpg.Connection", conn)
            await held.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(tenant_id)
            )
            yield _PgConnGateway(held)


class _PgConnGateway:
    """SqlGateway view over one held asyncpg connection (a tx scope)."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def fetch(self, sql: str, *args: Any) -> list[Row]:
        return list(await self._conn.fetch(sql, *args))

    async def fetchrow(self, sql: str, *args: Any) -> Row | None:
        return await self._conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await self._conn.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        return await self._conn.execute(sql, *args)

    def transaction(self, tenant_id: str) -> AbstractAsyncContextManager[_PgConnGateway]:
        return _pg_savepoint(self._conn)


@asynccontextmanager
async def _pg_savepoint(conn: asyncpg.Connection) -> AsyncIterator[_PgConnGateway]:
    """Nested PG scope = savepoint; the tenant GUC is already set local."""
    async with conn.transaction():
        yield _PgConnGateway(conn)
