"""Versioning handler — time-travel via event sourcing.

Event sourcing provides native time-travel: replay events up to a version
to see the state at that point. No separate versioning table needed.

Storage: takes an ``asyncpg.Pool`` (legacy wiring) or a
:class:`~corpus_kb.storage.gateway.SqlGateway` (portable wiring — the
DuckDB arm). ``handle_sql_tables`` is the one dialect branch: ``pg_tables``
exists only on Postgres; the gateway path reads ``information_schema``
and reports the ``duckdb`` owner. Every method runs inside
:func:`storage.tenant_conn.tenant_connection`. A missing ``event_store``
table still maps to the graceful empty result (the live table is the
eventsourcing library's ``corpusapplication_events``); every other
error propagates.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.handlers._singleton import SingletonSlot
from corpus_kb.storage.gateway import SqlGateway, is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection

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


def _missing_table(exc: Exception) -> bool:
    """True when the driver says the table itself is missing."""
    if isinstance(exc, asyncpg.UndefinedTableError):
        return True
    try:
        import duckdb
    except ImportError:
        return False
    return isinstance(exc, duckdb.CatalogException)


class VersioningHandler:
    """Handles versioning, tagging, branching via event store."""

    def __init__(self, store: asyncpg.Pool | SqlGateway) -> None:
        self._pool = store

    async def handle_list_versions(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List all aggregate versions from the event store."""
        try:
            async with tenant_connection(self._pool, tenant_id) as conn:
                rows = await conn.fetch(_VERSIONS_SQL)
                return [dict(r) for r in rows]
        except Exception as exc:
            if not _missing_table(exc):
                raise
            return []  # event_store table not created yet

    async def handle_get_stats(self, tenant_id: UUID) -> dict[str, Any]:
        """Get database statistics."""
        async with tenant_connection(self._pool, tenant_id) as conn:
            stats: dict[str, Any] = {
                table: await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                for table in _STATS_TABLES
            }
            try:
                stats["total_events"] = await conn.fetchval("SELECT COUNT(*) FROM event_store")
            except Exception as exc:
                if not _missing_table(exc):
                    raise
                # The failed probe aborts the transaction; the block exit
                # rolls it back. Keep later statements out of this block.
                stats["total_events"] = 0  # event_store table not created yet
            return stats

    async def handle_sql_tables(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List all tables in the schema."""
        sql = _DUCKDB_TABLES_SQL if is_gateway(self._pool) else _TABLES_SQL
        async with tenant_connection(self._pool, tenant_id) as conn:
            rows = await conn.fetch(sql)
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
        async with tenant_connection(self._pool, tenant_id) as conn:
            stats: dict[str, Any] = {
                name: await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                for name, table in counts.items()
            }
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

_versioning_handler = SingletonSlot[VersioningHandler](
    "VersioningHandler not initialized. Call set_versioning_handler() during startup."
)


def get_versioning_handler() -> VersioningHandler:
    return _versioning_handler.get()


def set_versioning_handler(handler: VersioningHandler) -> None:
    _versioning_handler.set(handler)


def reset_versioning_handler() -> None:
    _versioning_handler.reset()
