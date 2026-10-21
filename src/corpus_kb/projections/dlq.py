"""DLQ (Dead-Letter Queue) handler — retry failed projections.

Failed projections are recorded in projection_dlq. This handler
provides methods to list, retry, and clear failures.

Storage portability (program §3.1): all statements run through the
``SqlGateway`` — one transaction scope per call (tenant GUC on Postgres,
BEGIN..COMMIT on DuckDB), and the write units replay on a write-write
conflict via ``run_write_unit``.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections._gateway import resolve_gateway, run_write_unit
from corpus_kb.storage.gateway import SqlGateway

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class DLQHandler:
    """Manages the projection_dlq table for failed projection recovery.

    projection_dlq is FORCE-put under RLS by migration 011, so set_config and
    the statements that need it run in ONE transaction (the gateway scope).
    """

    def __init__(self, pool: asyncpg.Pool | None = None, gateway: SqlGateway | None = None) -> None:
        self._gateway = resolve_gateway(pool, gateway)

    async def record_failure(
        self,
        projection_name: str,
        tenant_id: UUID,
        event_id: UUID,
        event_type: str,
        error_message: str,
        error_stacktrace: str | None = None,
    ) -> None:
        """Record a failed projection to the DLQ."""
        args = (
            projection_name,
            str(tenant_id),
            str(event_id),
            event_type,
            error_message,
            error_stacktrace,
        )

        async def body(tx: SqlGateway) -> None:
            await tx.execute(
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
                *args,
            )

        await run_write_unit(self._gateway, tenant_id, body)
        logger.warning(
            "DLQ entry: projection=%s event=%s error=%s",
            projection_name,
            event_id,
            error_message,
        )

    async def list_failures(self, projection_name: str, tenant_id: UUID) -> list[dict[str, Any]]:
        """List unresolved DLQ entries for a projection."""
        async with self._gateway.transaction(str(tenant_id)) as tx:
            rows = await tx.fetch(
                """
                SELECT dlq_id, projection_name, event_id, event_type,
                       error_message, retry_count, created_at, resolved
                FROM projection_dlq
                WHERE projection_name = $1 AND resolved = FALSE
                ORDER BY created_at DESC, dlq_id
                """,
                projection_name,
            )
            return [dict(row) for row in rows]

    async def mark_resolved(self, dlq_id: UUID, tenant_id: UUID) -> None:
        """Mark a DLQ entry as resolved after successful retry."""

        async def body(tx: SqlGateway) -> None:
            await tx.execute(
                "UPDATE projection_dlq SET resolved = TRUE WHERE dlq_id = $1",
                str(dlq_id),
            )

        await run_write_unit(self._gateway, tenant_id, body)
        logger.info("DLQ entry %s resolved", dlq_id)

    async def is_permanent_failure(self, dlq_id: UUID, tenant_id: UUID) -> bool:
        """Check if a DLQ entry has exceeded max retries."""
        async with self._gateway.transaction(str(tenant_id)) as tx:
            count = await tx.fetchval(
                "SELECT retry_count FROM projection_dlq WHERE dlq_id = $1",
                str(dlq_id),
            )
        return count is not None and count >= MAX_RETRIES


# Singleton

_dlq_handler: DLQHandler | None = None


def get_dlq_handler(pool: asyncpg.Pool | None = None) -> DLQHandler:
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
