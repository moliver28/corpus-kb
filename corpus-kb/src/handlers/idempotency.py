"""Idempotency layer — prevents duplicate command execution.

Checks idempotency_keys table before executing a command.
If the key exists, returns cached result. If not, executes
and records the result for future deduplication.

Usage:
    from handlers.idempotency import IdempotencyChecker
    checker = IdempotencyChecker(pool)
    cached = await checker.check(tenant_id, command_id)
    if cached:
        return cached  # duplicate command, return cached result
    # ... execute command ...
    await checker.record(tenant_id, command_id, command_type, payload, result)
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class IdempotencyChecker:
    """Checks and records idempotency keys for command deduplication."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def check(
        self, tenant_id: UUID, command_id: UUID
    ) -> Optional[dict[str, Any]]:
        """Check if command already executed. Returns cached result or None."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            row = await conn.fetchrow(
                """
                SELECT command_type, command_payload, result, created_at
                FROM idempotency_keys
                WHERE idempotency_key = $1 AND expires_at > NOW()
                """,
                str(command_id),
            )
            if row:
                logger.info("Idempotency hit: command %s already executed", command_id)
                return dict(row) if row["result"] else None
            return None

    async def record(
        self,
        tenant_id: UUID,
        command_id: UUID,
        command_type: str,
        command_payload: dict[str, Any],
        result: Optional[dict[str, Any]] = None,
    ) -> None:
        """Record command execution for future deduplication."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            import json

            await conn.execute(
                """
                INSERT INTO idempotency_keys
                (idempotency_key, tenant_id, command_type, command_payload, result)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (idempotency_key, tenant_id) DO NOTHING
                """,
                str(command_id),
                str(tenant_id),
                command_type,
                json.dumps(command_payload, default=str),
                json.dumps(result, default=str) if result else None,
            )


# ============================================================================
# Singleton
# ============================================================================

_checker: Optional[IdempotencyChecker] = None


def get_idempotency_checker(pool: Optional[asyncpg.Pool] = None) -> IdempotencyChecker:
    """Get or create the singleton IdempotencyChecker."""
    global _checker
    if _checker is None:
        if pool is None:
            raise RuntimeError("IdempotencyChecker requires an asyncpg pool")
        _checker = IdempotencyChecker(pool)
    return _checker


def set_idempotency_checker(checker: IdempotencyChecker) -> None:
    """Set the singleton (for server wiring)."""
    global _checker
    _checker = checker


def reset_idempotency_checker() -> None:
    """Reset the singleton (for testing)."""
    global _checker
    _checker = None
