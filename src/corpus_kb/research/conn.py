"""The research domain's one storage seam (storage portability program §13).

Research modules open one tenant-scoped session per unit of work: the
Postgres path keeps :func:`tenant_connection` byte-identical (pooled
connection, tenant GUC, one transaction), the DuckDB path takes the
injected :class:`SqlGateway`'s transaction scope (BEGIN..COMMIT; the
engine allows one writer, so the scope IS the serialization point).
Every session object exposes the same four async calls — ``fetch``,
``fetchrow``, ``fetchval``, ``execute`` — typed by :class:`SqlConn`, the
structural view both drivers satisfy (positional-only parameters, so the
asyncpg ``query`` name and the gateway ``sql`` name never conflict).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol, TypeAlias, runtime_checkable

import asyncpg

from corpus_kb.errors import ConfigError
from corpus_kb.storage.dialect import DUCKDB, POSTGRES, Dialect
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.gateway import PostgresGateway, SqlGateway
from corpus_kb.storage.tenant_conn import tenant_connection

__all__ = [
    "SqlConn",
    "Store",
    "dialect_of",
    "json_cast",
    "json_value",
    "resolve_store",
    "tenant_scope",
]

#: The store argument every research entry point accepts: today's asyncpg
#: pool (Postgres path, unchanged callers) or an injected SqlGateway
#: (DuckDB path). Dispatch happens once, inside :func:`tenant_scope`.
Store: TypeAlias = asyncpg.Pool | SqlGateway


@runtime_checkable
class SqlConn(Protocol):
    """The four-call session surface every research function runs against."""

    async def fetch(self, sql: str, /, *args: object) -> list[Any]:
        """Run one read and return every matching row."""
        ...

    async def fetchrow(self, sql: str, /, *args: object) -> Any | None:
        """Run one read and return the first row, or None."""
        ...

    async def fetchval(self, sql: str, /, *args: object) -> Any:
        """Run one read and return the first column of the first row."""
        ...

    async def execute(self, sql: str, /, *args: object) -> object:
        """Run one write and return the driver status tag."""
        ...


def dialect_of(store: Store) -> Dialect:
    """The SQL dialect of the injected store (gateway = DuckDB, pool = PG)."""
    return DUCKDB if isinstance(store, DuckDbGateway) else POSTGRES


def resolve_store(store: Store | None, gateway: SqlGateway | None = None) -> SqlGateway:
    """The injected gateway, else the driver over the pool.

    The projections-port constructor shape, typed for the research union:
    ``resolve_store(pool)`` wraps Postgres, ``resolve_store(None, gw)``
    passes a gateway through, and neither given raises typed.
    """
    if gateway is not None:
        return gateway
    if store is None:
        raise ConfigError("Research components need a SqlGateway or an asyncpg pool.")
    if isinstance(store, asyncpg.Pool):
        return PostgresGateway(store)
    return store


@asynccontextmanager
async def tenant_scope(store: Store, tenant_id: Any) -> AsyncIterator[SqlConn]:
    """Open one tenant-scoped session: RLS GUC on Postgres, tx on DuckDB."""
    if isinstance(store, asyncpg.Pool):
        async with tenant_connection(store, tenant_id) as conn:
            yield conn
    else:
        async with store.transaction(str(tenant_id)) as tx:
            yield tx


def json_cast(dialect: Dialect, idx: str) -> str:
    """The JSON parameter cast: ``::jsonb`` on Postgres, ``::JSON`` on DuckDB.

    Postgres keeps its byte-identical jsonb cast; DuckDB stores JSON and
    reads the column back as text, so read sites normalize with
    :func:`json_value`.
    """
    return f"{idx}::JSON" if dialect.is_duckdb else f"{idx}::jsonb"


def json_value(raw: object) -> Any:
    """One JSON column read, engine-normalized: decoded object or parsed text.

    asyncpg decodes ``jsonb`` to Python objects; DuckDB returns the column
    as its text form. Null and already-decoded values pass through.
    """
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


_DUCK_LIST_CAST = {"uuid": "VARCHAR", "bigint": "BIGINT"}


def ids_any(placeholder: str, column: str, duck: bool, kind: str) -> str:
    """List membership over an id column: asyncpg ``ANY`` vs ``list_contains``.

    Postgres keeps its byte-identical ``= ANY($n::<kind>[])`` form; DuckDB
    has no ``= ANY``, so the twin casts the bound list and uses the engine's
    ``list_contains`` (id columns are VARCHAR/BIGINT there — no UUID lists).
    """
    if duck:
        return f"list_contains({placeholder}::{_DUCK_LIST_CAST[kind]}[], {column})"
    return f"{column} = ANY({placeholder}::{kind}[])"
