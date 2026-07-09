"""Versioning handler — time-travel via event sourcing.

Event sourcing provides native time-travel: replay events up to a version
to see the state at that point. No separate versioning table needed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class VersioningHandler:
    """Handles versioning, tagging, branching via event store."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def handle_list_versions(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List all aggregate versions from the event store."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            try:
                rows = await conn.fetch(
                    """SELECT aggregate_id, MAX(version) as max_version, COUNT(*) as event_count,
                              MIN(created_at) as first_event, MAX(created_at) as last_event
                       FROM event_store GROUP BY aggregate_id ORDER BY last_event DESC"""
                )
                return [dict(r) for r in rows]
            except Exception:
                return []  # event_store table not created yet

    async def handle_get_stats(self, tenant_id: UUID) -> dict[str, Any]:
        """Get database statistics."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            stats = {}
            for table in [
                "documents",
                "chunks",
                "chunks_vectors",
                "entities",
                "relations",
                "tags",
            ]:
                stats[table] = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            try:
                stats["total_events"] = await conn.fetchval(
                    "SELECT COUNT(*) FROM event_store"
                )
            except Exception:
                stats["total_events"] = 0  # event_store table not created yet
            return stats

    async def handle_sql_tables(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List all tables in the schema."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            rows = await conn.fetch(
                """SELECT tablename as name, tableowner as owner FROM pg_tables
                   WHERE schemaname = 'public' ORDER BY tablename"""
            )
            return [dict(r) for r in rows]

    async def handle_query_document_stats(self, tenant_id: UUID) -> dict[str, Any]:
        """Get aggregate document statistics."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
            )
            total_docs = await conn.fetchval("SELECT COUNT(*) FROM documents")
            total_chunks = await conn.fetchval("SELECT COUNT(*) FROM chunks")
            total_vectors = await conn.fetchval("SELECT COUNT(*) FROM chunks_vectors")
            total_entities = await conn.fetchval("SELECT COUNT(*) FROM entities")
            total_relations = await conn.fetchval("SELECT COUNT(*) FROM relations")
            by_type = await conn.fetch(
                """SELECT source_type, COUNT(*) as count FROM documents
                   WHERE tenant_id = $1 GROUP BY source_type""",
                str(tenant_id),
            )
            return {
                "total_documents": total_docs,
                "total_chunks": total_chunks,
                "total_vectors": total_vectors,
                "total_entities": total_entities,
                "total_relations": total_relations,
                "by_source_type": [dict(r) for r in by_type],
            }


# ============================================================================
# Singleton
# ============================================================================

_versioning_handler: Optional["VersioningHandler"] = None


def get_versioning_handler() -> "VersioningHandler":
    global _versioning_handler
    if _versioning_handler is None:
        raise RuntimeError(
            "VersioningHandler not initialized. Call set_versioning_handler() during startup."
        )
    return _versioning_handler


def set_versioning_handler(handler: "VersioningHandler") -> None:
    global _versioning_handler
    _versioning_handler = handler


def reset_versioning_handler() -> None:
    global _versioning_handler
    _versioning_handler = None
