"""Projection checkpoint manager — tracks projection state for crash recovery.

Each projection maintains checkpoint rows in projection_checkpoints (which
migration 011 FORCE-puts under RLS and migration 016 extends with a
``last_sequence BIGINT`` column). Positions are the eventsourcing library's
monotonic ``notification_id`` over the event store — never wall-clock
timestamps, which cannot order same-timestamp events.

Storage portability (program §3.1): every statement runs inside ONE
``SqlGateway`` transaction scope. On Postgres that scope sets the tenant GUC
(RLS stays defense-in-depth, byte-identical to the pre-port
``tenant_connection`` path); on DuckDB the scope is BEGIN..COMMIT and the
write unit replays on a write-write conflict (``run_write_unit``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections._gateway import resolve_gateway, run_write_unit
from corpus_kb.storage.gateway import SqlGateway

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages projection_checkpoints table for catch-up subscriptions."""

    def __init__(self, pool: asyncpg.Pool | None = None, gateway: SqlGateway | None = None) -> None:
        self._gateway = resolve_gateway(pool, gateway)

    async def get_checkpoint(self, projection_name: str, tenant_id: UUID) -> dict[str, Any] | None:
        """Get the last processed position for a projection."""
        async with self._gateway.transaction(str(tenant_id)) as tx:
            row = await tx.fetchrow(
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

        async def body(tx: SqlGateway) -> None:
            await tx.execute(
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

        await run_write_unit(self._gateway, tenant_id, body)
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
