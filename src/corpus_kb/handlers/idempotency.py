"""Idempotency layer — prevents duplicate command execution.

Checks idempotency_keys table before executing a command.
If the key exists, returns cached result. If not, executes
and records the result for future deduplication.

Storage: the checker takes an ``asyncpg.Pool`` (legacy wiring) or a
:class:`~corpus_kb.storage.gateway.SqlGateway` (portable wiring — the
DuckDB arm). Both run every statement inside one tenant transaction; the
gateway's ``transaction(tenant_id)`` owns the Postgres tenant GUC, so no
``set_config`` appears on the portable path.

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

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.storage.gateway import SqlGateway

logger = logging.getLogger(__name__)

_CHECK_SQL = """
    SELECT command_type, command_payload, result, created_at
    FROM idempotency_keys
    WHERE idempotency_key = $1 AND expires_at > NOW()
"""
# The gateway arm reads created_at as an ISO string: DuckDB needs the
# optional pytz wheel to materialize TIMESTAMPTZ values in Python (the
# Postgres shape keeps the datetime — byte-identical as shipped).
_CHECK_SQL_GATEWAY = """
    SELECT command_type, command_payload, result, created_at::VARCHAR AS created_at
    FROM idempotency_keys
    WHERE idempotency_key = $1 AND expires_at > NOW()
"""
_RECORD_SQL = """
    INSERT INTO idempotency_keys
    (idempotency_key, tenant_id, command_type, command_payload, result)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (idempotency_key, tenant_id) DO NOTHING
"""


def _is_gateway(store: asyncpg.Pool | SqlGateway) -> bool:
    """True for SqlGateway stores: a transaction scope, no pool acquire.

    Anything else (asyncpg pools, MagicMock pools, fetch-only test fakes)
    stays on the shipped tenant_connection path.
    """
    return not hasattr(store, "acquire") and hasattr(store, "transaction")


class IdempotencyChecker:
    """Checks and records idempotency keys for command deduplication."""

    def __init__(self, store: asyncpg.Pool | SqlGateway) -> None:
        self._store = store

    async def check(self, tenant_id: UUID, command_id: UUID) -> dict[str, Any] | None:
        """Check if command already executed. Returns cached result or None.

        The tenant context and the read share ONE transaction: with
        is_local=true the setting lives only inside it, and the RLS policy
        casts current_setting('app.current_tenant_id') to UUID — an
        autocommit read after a bare set_config sees an empty value and
        fails the cast (live e2e round, W5C).
        """
        if _is_gateway(self._store):
            async with self._store.transaction(str(tenant_id)) as gw:
                row = await gw.fetchrow(_CHECK_SQL_GATEWAY, str(command_id))
                if row:
                    logger.info("Idempotency hit: command %s already executed", command_id)
                    return dict(row) if row["result"] else None
                return None
        async with self._store.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            row = await conn.fetchrow(_CHECK_SQL, str(command_id))
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
        result: dict[str, Any] | None = None,
    ) -> None:
        """Record command execution for future deduplication.

        One transaction holds the tenant context and the INSERT: the RLS
        WITH CHECK policy needs the tenant value live in the same
        transaction (see :meth:`check`). The ON CONFLICT guard keeps the
        FIRST result — a replayed record cannot overwrite it.
        """
        encoded_payload = json.dumps(command_payload, default=str)
        encoded_result = json.dumps(result, default=str) if result else None
        if _is_gateway(self._store):
            async with self._store.transaction(str(tenant_id)) as gw:
                await gw.execute(
                    _RECORD_SQL,
                    str(command_id),
                    str(tenant_id),
                    command_type,
                    encoded_payload,
                    encoded_result,
                )
            return
        async with self._store.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                str(tenant_id),
            )
            await conn.execute(
                _RECORD_SQL,
                str(command_id),
                str(tenant_id),
                command_type,
                encoded_payload,
                encoded_result,
            )


# ============================================================================
# Singleton
# ============================================================================

_checker: IdempotencyChecker | None = None


def get_idempotency_checker(pool: asyncpg.Pool | None = None) -> IdempotencyChecker:
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
