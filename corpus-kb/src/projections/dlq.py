"""DLQ (Dead-Letter Queue) handler — retry failed projections.

Failed projections are recorded in projection_dlq. This handler
provides methods to list, retry, and clear failures.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class DLQHandler:
    """Manages the projection_dlq table for failed projection recovery."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_failure(
        self,
        projection_name: str,
        tenant_id: UUID,
        event_id: UUID,
        event_type: str,
        error_message: str,
        error_stacktrace: Optional[str] = None,
    ) -> None:
        """Record a failed projection to the DLQ."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            await conn.execute(
                """
                INSERT INTO projection_dlq
                (projection_name, tenant_id, event_id, event_type,
                 error_message, error_stacktrace)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (projection_name, tenant_id, event_id)
                DO UPDATE SET
                    retry_count = projection_dlq.retry_count + 1,
                    error_message = $5,
                    error_stacktrace = $6
                """,
                projection_name,
                str(tenant_id),
                str(event_id),
                event_type,
                error_message,
                error_stacktrace,
            )
            logger.warning(
                "DLQ entry: projection=%s event=%s error=%s",
                projection_name,
                event_id,
                error_message,
            )

    async def list_failures(
        self, projection_name: str, tenant_id: UUID
    ) -> list[dict[str, Any]]:
        """List unresolved DLQ entries for a projection."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            rows = await conn.fetch(
                """
                SELECT dlq_id, projection_name, event_id, event_type,
                       error_message, retry_count, created_at, resolved
                FROM projection_dlq
                WHERE projection_name = $1 AND resolved = FALSE
                ORDER BY created_at DESC
                """,
                projection_name,
            )
            return [dict(row) for row in rows]

    async def mark_resolved(self, dlq_id: UUID, tenant_id: UUID) -> None:
        """Mark a DLQ entry as resolved after successful retry."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            await conn.execute(
                "UPDATE projection_dlq SET resolved = TRUE WHERE dlq_id = $1",
                str(dlq_id),
            )
            logger.info("DLQ entry %s resolved", dlq_id)

    async def is_permanent_failure(self, dlq_id: UUID, tenant_id: UUID) -> bool:
        """Check if a DLQ entry has exceeded max retries."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            count = await conn.fetchval(
                "SELECT retry_count FROM projection_dlq WHERE dlq_id = $1",
                str(dlq_id),
            )
            return count is not None and count >= MAX_RETRIES


# Singleton

_dlq_handler: Optional[DLQHandler] = None


def get_dlq_handler(pool: Optional[asyncpg.Pool] = None) -> DLQHandler:
    global _dlq_handler
    if _dlq_handler is None:
        if pool is None:
            raise RuntimeError("DLQHandler requires an asyncpg pool")
        _dlq_handler = DLQHandler(pool)
    return _dlq_handler


def set_dlq_handler(handler: DLQHandler) -> None:
    global _dlq_handler
    _dlq_handler = handler


def reset_dlq_handler() -> None:
    global _dlq_handler
    _dlq_handler = None
