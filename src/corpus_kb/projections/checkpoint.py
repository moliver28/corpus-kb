"""Projection checkpoint manager — tracks projection state for crash recovery.

Each projection maintains checkpoint rows in projection_checkpoints (which
migration 011 FORCE-puts under RLS and migration 016 extends with a
``last_sequence BIGINT`` column). Positions are the eventsourcing library's
monotonic ``notification_id`` bigserial over its ``stored_events`` table —
never wall-clock timestamps, which cannot order same-timestamp events.

RLS note (todo-11 STEP 0, item (e)): migration 011 FORCEs row-level security
on projection_checkpoints, and asyncpg auto-commits every unwrapped
statement. A separate ``SELECT set_config(...)`` statement is committed and
gone before the write runs, so the write violates the WITH CHECK policy.
Every method here therefore wraps set_config AND its statements in ONE
transaction via ``tenant_connection()``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages projection_checkpoints table for catch-up subscriptions."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_checkpoint(self, projection_name: str, tenant_id: UUID) -> dict[str, Any] | None:
        """Get the last processed position for a projection."""
        async with tenant_connection(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT last_event_id, last_event_timestamp, checkpoint_timestamp,
                       last_sequence
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
        last_event_timestamp: datetime,
        last_sequence: int | None = None,
    ) -> None:
        """Update or insert checkpoint after processing events.

        ``last_sequence`` is the global notification_id high-water mark the
        projection has consumed. When omitted (legacy callers), the stored
        position is preserved. ``last_event_timestamp`` must be an aware
        datetime (the column is TIMESTAMPTZ).
        """
        async with tenant_connection(self._pool, tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO projection_checkpoints
                (projection_name, tenant_id, last_event_id, last_event_timestamp,
                 last_sequence)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (projection_name, tenant_id)
                DO UPDATE SET
                    last_event_id = $3,
                    last_event_timestamp = $4,
                    checkpoint_timestamp = NOW(),
                    last_sequence = COALESCE($5, projection_checkpoints.last_sequence)
                """,
                projection_name,
                str(tenant_id),
                str(last_event_id),
                last_event_timestamp,
                last_sequence,
            )
        logger.debug(
            "Checkpoint updated: %s tenant=%s sequence=%s",
            projection_name,
            tenant_id,
            last_sequence,
        )


# Singleton

_checkpoint_mgr: CheckpointManager | None = None


def get_checkpoint_manager(pool: asyncpg.Pool | None = None) -> CheckpointManager:
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
