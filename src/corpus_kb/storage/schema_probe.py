"""Engine-neutral schema probes (issue #330 Part 1).

One probe pair for both engines, so feature modules stop hand-rolling
Postgres-only ``to_regclass`` checks. Postgres keeps the shipped
``to_regclass('public.<table>')`` form; DuckDB reads ``information_schema``
with ``table_schema = current_schema()`` (DuckDB names the catalog after the
database file, so a hard-coded schema would lie on every file name).

Both functions accept the shared store union (asyncpg pool or any
``SqlGateway``) and answer a plain boolean. Identifiers come from repo call
sites (constant table names), and :mod:`storage.sql_ident` validates them
before interpolation — no caller text reaches the SQL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from corpus_kb.storage.gateway import is_gateway
from corpus_kb.storage.sql_ident import check_identifier, sql_literal

if TYPE_CHECKING:
    import asyncpg

    from corpus_kb.storage.gateway import SqlConn


async def tables_exist(store: asyncpg.Pool | asyncpg.Connection | SqlConn, *tables: str) -> bool:
    """True when EVERY named table exists on the connected engine.

    One engine probe for the whole set: feature modules probe their table
    family in one call (the topics-writer convention — one migration file
    creates the family, one probe covers it).
    """
    if not tables:
        return True
    names = [check_identifier(table) for table in tables]
    if is_gateway(store):
        literals = ", ".join(sql_literal(name) for name in names)
        sql = (
            f"SELECT count(*) = {len(names)} FROM information_schema.tables "
            f"WHERE table_schema = current_schema() AND table_name IN ({literals})"
        )
        return bool(await store.fetchval(sql))
    if hasattr(store, "acquire"):
        pool = cast("asyncpg.Pool", store)
        async with pool.acquire() as conn:
            probes = ", ".join(f"to_regclass('public.{name}') IS NOT NULL" for name in names)
            return bool(await conn.fetchval(f"SELECT {probes}"))
    conn = cast("asyncpg.Connection", store)
    probes = ", ".join(f"to_regclass('public.{name}') IS NOT NULL" for name in names)
    return bool(await conn.fetchval(f"SELECT {probes}"))


async def column_exists(
    store: asyncpg.Pool | asyncpg.Connection | SqlConn, table: str, column: str
) -> bool:
    """True when one column exists on the connected engine."""
    table_name = check_identifier(table)
    column_name = check_identifier(column)
    if is_gateway(store):
        sql = (
            "SELECT count(*) > 0 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            f"AND table_name = {sql_literal(table_name)} "
            f"AND column_name = {sql_literal(column_name)}"
        )
        return bool(await store.fetchval(sql))
    sql = (
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.columns"
        "  WHERE table_schema = 'public'"
        f"   AND table_name = {sql_literal(table_name)}"
        f"   AND column_name = {sql_literal(column_name)})"
    )
    if hasattr(store, "acquire"):
        pool = cast("asyncpg.Pool", store)
        async with pool.acquire() as conn:
            return bool(await conn.fetchval(sql))
    conn = cast("asyncpg.Connection", store)
    return bool(await conn.fetchval(sql))
