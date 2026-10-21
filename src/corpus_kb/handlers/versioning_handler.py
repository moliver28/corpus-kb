"""Versioning handler — time-travel via event sourcing.

Event sourcing provides native time-travel: replay events up to a version
to see the state at that point. No separate versioning table needed.

Storage: takes an ``asyncpg.Pool`` (legacy wiring) or a
:class:`~corpus_kb.storage.gateway.SqlGateway` (portable wiring — the
DuckDB arm). ``handle_sql_tables`` is the one dialect branch: ``pg_tables``
exists only on Postgres; the gateway path reads ``information_schema``
and reports the ``duckdb`` owner. ``handle_list_versions`` and the
``total_events`` probe keep their shipped try/except shape on both
storages — the event store's live table is the eventsourcing library's
(``corpusapplication_events``), not ``event_store``.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.storage.gateway import SqlGateway

logger = logging.getLogger(__name__)

_TABLES_SQL = """SELECT tablename as name, tableowner as owner FROM pg_tables
   WHERE schemaname = 'public' ORDER BY tablename"""
_DUCKDB_TABLES_SQL = """SELECT table_name as name, 'duckdb' as owner
   FROM information_schema.tables
   WHERE table_schema = 'main' ORDER BY table_name"""
_VERSIONS_SQL = """SELECT aggregate_id, MAX(version) as max_version, COUNT(*) as event_count,
              MIN(created_at) as first_event, MAX(created_at) as last_event
       FROM event_store GROUP BY aggregate_id ORDER BY last_event DESC"""
_STATS_TABLES = [
    "documents",
    "chunks",
    "chunks_vectors",
    "entities",
    "relations",
    "tags",
]


def _is_gateway(store: asyncpg.Pool | SqlGateway) -> bool:
    """True for SqlGateway stores: a transaction scope, no pool acquire.

    Anything else (asyncpg pools, MagicMock pools, fetch-only test fakes)
    stays on the shipped tenant_connection path.
    """
    return not hasattr(store, "acquire") and hasattr(store, "transaction")


class VersioningHandler:
    """Handles versioning, tagging, branching via event store."""

    def __init__(self, store: asyncpg.Pool | SqlGateway) -> None:
        self._pool = store

    async def handle_list_versions(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List all aggregate versions from the event store."""
        if _is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                try:
                    rows = await gw.fetch(_VERSIONS_SQL)
                    return [dict(r) for r in rows]
                except Exception:
                    return []  # event_store table not created yet
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            try:
                rows = await conn.fetch(_VERSIONS_SQL)
                return [dict(r) for r in rows]
            except Exception:
                return []  # event_store table not created yet

    async def handle_get_stats(self, tenant_id: UUID) -> dict[str, Any]:
        """Get database statistics."""
        if _is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                stats = {
                    table: await gw.fetchval(f"SELECT COUNT(*) FROM {table}")
                    for table in _STATS_TABLES
                }
                try:
                    stats["total_events"] = await gw.fetchval("SELECT COUNT(*) FROM event_store")
                except Exception:
                    stats["total_events"] = 0  # event_store table not created yet
                return stats
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            stats: dict[str, Any] = {}
            for table in _STATS_TABLES:
                stats[table] = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            try:
                stats["total_events"] = await conn.fetchval("SELECT COUNT(*) FROM event_store")
            except Exception:
                stats["total_events"] = 0  # event_store table not created yet
            return stats

    async def handle_sql_tables(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List all tables in the schema."""
        if _is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                rows = await gw.fetch(_DUCKDB_TABLES_SQL)
                return [dict(r) for r in rows]
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            rows = await conn.fetch(_TABLES_SQL)
            return [dict(r) for r in rows]

    async def handle_query_document_stats(self, tenant_id: UUID) -> dict[str, Any]:
        """Get aggregate document statistics."""
        counts = {
            "total_documents": "documents",
            "total_chunks": "chunks",
            "total_vectors": "chunks_vectors",
            "total_entities": "entities",
            "total_relations": "relations",
        }
        if _is_gateway(self._pool):
            async with self._pool.transaction(str(tenant_id)) as gw:
                stats = {
                    name: await gw.fetchval(f"SELECT COUNT(*) FROM {t}")
                    for name, t in counts.items()
                }
                by_type = await gw.fetch(
                    """SELECT source_type, COUNT(*) as count FROM documents
                       WHERE tenant_id = $1 GROUP BY source_type""",
                    str(tenant_id),
                )
                stats["by_source_type"] = [dict(r) for r in by_type]
                return stats
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            stats: dict[str, Any] = {}
            for name, table in counts.items():
                stats[name] = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            by_type = await conn.fetch(
                """SELECT source_type, COUNT(*) as count FROM documents
                   WHERE tenant_id = $1 GROUP BY source_type""",
                str(tenant_id),
            )
            stats["by_source_type"] = [dict(r) for r in by_type]
            return stats


# ============================================================================
# Singleton
# ============================================================================

_versioning_handler: VersioningHandler | None = None


def get_versioning_handler() -> VersioningHandler:
    global _versioning_handler
    if _versioning_handler is None:
        raise RuntimeError(
            "VersioningHandler not initialized. Call set_versioning_handler() during startup."
        )
    return _versioning_handler


def set_versioning_handler(handler: VersioningHandler) -> None:
    global _versioning_handler
    _versioning_handler = handler


def reset_versioning_handler() -> None:
    global _versioning_handler
    _versioning_handler = None
