"""Ingest-queue persistence: enqueue, read, cancel over asyncpg.

One row = one file waiting for a worker. The tenant GUC rides every
connection (the table is FORCE-row-level-security, migration 037), all
SQL is parameterized, and reads carry deterministic tiebreakers
(``ORDER BY queued_at, id``) so harnesses can snapshot them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from corpus_kb.errors import ConflictError

if TYPE_CHECKING:
    import asyncpg

_ROW_COLUMNS = (
    "id::text, tenant_id::text, path, title, project_id::text, profile, "
    "strategy_config, status, attempts, max_attempts, last_error, "
    "run_id::text, content_hash, claimed_by, heartbeat_at, queued_at, "
    "started_at, finished_at, updated_at"
)

# The partial unique index backs this probe (one ACTIVE row per hash); the
# INSERT's ON CONFLICT DO NOTHING closes the race between probe and insert.
FIND_ACTIVE_SQL = f"""
SELECT {_ROW_COLUMNS}
FROM ingest_queue
WHERE tenant_id = $1 AND content_hash = $2
  AND status IN ('pending', 'running')
ORDER BY queued_at, id
LIMIT 1
"""

INSERT_SQL = f"""
INSERT INTO ingest_queue (
    tenant_id, path, title, project_id, profile, strategy_config,
    status, attempts, max_attempts, content_hash
) VALUES ($1, $2, $3, $4::uuid, $5, $6::jsonb, 'pending', 0, $7, $8)
ON CONFLICT DO NOTHING
RETURNING {_ROW_COLUMNS}
"""

GET_SQL = f"""
SELECT {_ROW_COLUMNS}
FROM ingest_queue
WHERE tenant_id = $1 AND id = $2::uuid
"""

LIST_ACTIVE_SQL = f"""
SELECT {_ROW_COLUMNS}
FROM ingest_queue
WHERE tenant_id = $1 AND status IN ('pending', 'running')
ORDER BY queued_at, id
LIMIT $2
"""

CANCEL_SQL = f"""
UPDATE ingest_queue
SET status = 'cancelled', finished_at = now(), updated_at = now()
WHERE tenant_id = $1 AND id = $2::uuid AND status IN ('pending', 'running')
RETURNING {_ROW_COLUMNS}
"""


class IngestQueueStore:
    """Queue writes, reads, and the cancel path over one asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool, tenant_id: str) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def enqueue(
        self,
        *,
        path: str,
        title: str | None,
        project_id: str | None,
        profile: str | None,
        strategy_config: dict[str, object],
        max_attempts: int,
        content_hash: str,
    ) -> tuple[dict[str, Any], bool]:
        """Insert one pending row, or return the active twin.

        Returns ``(row, deduplicated)``: ``deduplicated`` is True when an
        active row with the same content hash already existed (or won the
        insert race), and the returned row is that existing row.
        """
        existing = await self._fetch_one(FIND_ACTIVE_SQL, self._tenant_id, content_hash)
        if existing is not None:
            return existing, True
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            inserted = await conn.fetchrow(
                INSERT_SQL,
                self._tenant_id,
                path,
                title,
                project_id,
                profile,
                json.dumps(strategy_config),
                max_attempts,
                content_hash,
            )
        if inserted is not None:
            return dict(inserted), False
        # Lost the insert race against the partial unique index: re-read.
        raced = await self._fetch_one(FIND_ACTIVE_SQL, self._tenant_id, content_hash)
        if raced is not None:
            return raced, True
        raise ConflictError(
            f"The active row for content hash {content_hash} was cancelled "
            "during the enqueue race. Enqueue again."
        )

    async def get(self, queue_id: str) -> dict[str, Any] | None:
        """One queue row by id, or None when the tenant has no such row."""
        return await self._fetch_one(GET_SQL, self._tenant_id, queue_id)

    async def list_active(self, limit: int = 50) -> list[dict[str, Any]]:
        """Pending + running rows, oldest queued first, bounded by limit."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            rows = await conn.fetch(LIST_ACTIVE_SQL, self._tenant_id, limit)
        return [dict(row) for row in rows]

    async def cancel(self, queue_id: str) -> dict[str, Any] | None:
        """Cancel one pending/running row; None when nothing matched.

        A None return means "no active row with this id" — the caller
        distinguishes not-found from already-terminal with ``get``.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            row = await conn.fetchrow(CANCEL_SQL, self._tenant_id, queue_id)
        return dict(row) if row is not None else None

    async def _fetch_one(self, sql: str, *args: object) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            row = await conn.fetchrow(sql, *args)
        return dict(row) if row is not None else None
