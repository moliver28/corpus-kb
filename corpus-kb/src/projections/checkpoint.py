"""Projection checkpoint manager — tracks projection state for crash recovery.

Each projection maintains a per-tenant checkpoint. On startup,
projections read their checkpoint and process events from that point.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages projection_checkpoints table for catch-up subscriptions."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_checkpoint(
        self, projection_name: str, tenant_id: UUID
    ) -> Optional[dict[str, Any]]:
        """Get the last processed event for a projection."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            row = await conn.fetchrow(
                """
                SELECT last_event_id, last_event_timestamp, checkpoint_timestamp
                FROM projection_checkpoints
                WHERE projection_name = $1
                """,
                projection_name,
            )
            return dict(row) if row else None

    async def update_checkpoint(
        self,
        projection_name: str,
        tenant_id: UUID,
        last_event_id: UUID,
        last_event_timestamp: str,
    ) -> None:
        """Update or insert checkpoint after processing an event."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            await conn.execute(
                """
                INSERT INTO projection_checkpoints
                (projection_name, tenant_id, last_event_id, last_event_timestamp)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (projection_name, tenant_id)
                DO UPDATE SET
                    last_event_id = $3,
                    last_event_timestamp = $4,
                    checkpoint_timestamp = NOW()
                """,
                projection_name,
                str(tenant_id),
                str(last_event_id),
                last_event_timestamp,
            )
            logger.debug(
                "Checkpoint updated: %s tenant=%s event=%s",
                projection_name,
                tenant_id,
                last_event_id,
            )

    async def get_events_since(
        self,
        tenant_id: UUID,
        last_event_timestamp: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Get events since the last checkpoint timestamp."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            if last_event_timestamp:
                rows = await conn.fetch(
                    """
                    SELECT event_id, aggregate_id, event_type, payload, created_at
                    FROM event_store
                    WHERE created_at > $1
                    ORDER BY created_at ASC
                    LIMIT $2
                    """,
                    last_event_timestamp,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT event_id, aggregate_id, event_type, payload, created_at
                    FROM event_store
                    ORDER BY created_at ASC
                    LIMIT $1
                    """,
                    limit,
                )
            return [dict(row) for row in rows]


# Singleton

_checkpoint_mgr: Optional[CheckpointManager] = None


def get_checkpoint_manager(pool: Optional[asyncpg.Pool] = None) -> CheckpointManager:
    global _checkpoint_mgr
    if _checkpoint_mgr is None:
        if pool is None:
            raise RuntimeError("CheckpointManager requires an asyncpg pool")
        _checkpoint_mgr = CheckpointManager(pool)
    return _checkpoint_mgr


def set_checkpoint_manager(mgr: CheckpointManager) -> None:
    global _checkpoint_mgr
    _checkpoint_mgr = mgr


def reset_checkpoint_manager() -> None:
    global _checkpoint_mgr
    _checkpoint_mgr = None
