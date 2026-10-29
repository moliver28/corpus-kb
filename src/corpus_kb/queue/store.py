"""Ingest-queue persistence: enqueue, read, cancel over asyncpg.

One row = one file waiting for a worker. The tenant GUC rides every
connection (the table is FORCE-row-level-security, migration 037), all
SQL is parameterized, and reads carry deterministic tiebreakers
(``ORDER BY queued_at, id``) so harnesses can snapshot them.

Every statement lives in ``queue.sql``; this module owns the call
patterns. The Wave 2C worker ops (claim, reclaim, heartbeat, release,
finish, fail) sit beside the Wave 1 ops so all queue table access keeps
one home.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from corpus_kb.errors import ConflictError

from .sql import (
    CANCEL_SQL,
    CLAIM_SQL,
    FAIL_SQL,
    FIND_ACTIVE_SQL,
    FINISH_SQL,
    GET_SQL,
    HEARTBEAT_SQL,
    INSERT_SQL,
    LIST_ACTIVE_SQL,
    RECLAIM_STALE_SQL,
    RELEASE_SQL,
)

if TYPE_CHECKING:
    import asyncpg

__all__ = [
    "CANCEL_SQL",
    "CLAIM_SQL",
    "FAIL_SQL",
    "FIND_ACTIVE_SQL",
    "FINISH_SQL",
    "GET_SQL",
    "HEARTBEAT_SQL",
    "INSERT_SQL",
    "LIST_ACTIVE_SQL",
    "RECLAIM_STALE_SQL",
    "RELEASE_SQL",
    "IngestQueueStore",
]


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
        return await self._fetch_many(LIST_ACTIVE_SQL, self._tenant_id, limit)

    async def cancel(self, queue_id: str) -> dict[str, Any] | None:
        """Cancel one pending/running row; None when nothing matched.

        A None return means "no active row with this id" — the caller
        distinguishes not-found from already-terminal with ``get``.
        """
        return await self._fetch_one(CANCEL_SQL, self._tenant_id, queue_id)

    # --- Wave 2C worker ops --------------------------------------------------

    async def claim(self, claimed_by: str, *, claimed_before: datetime) -> dict[str, Any] | None:
        """Claim the oldest pending row (FOR UPDATE SKIP LOCKED), or None.

        ``claimed_before`` is the pass watermark: a row first claimed at
        or after it waits for a later pass, so one drain pass gives each
        row one attempt.
        """
        return await self._fetch_one(CLAIM_SQL, self._tenant_id, claimed_by, claimed_before)

    async def reclaim_stale(self, stale_lease_seconds: float) -> list[dict[str, Any]]:
        """Rows back to pending when their heartbeat went quiet.

        attempts stays: a stale lease is a crash, not a counted failure.
        Rows answer oldest queued first (the worker re-sorts the RETURNING
        set, which PostgreSQL does not order).
        """
        rows = await self._fetch_many(RECLAIM_STALE_SQL, self._tenant_id, stale_lease_seconds)
        return sorted(rows, key=lambda r: (str(r.get("queued_at")), str(r.get("id"))))

    async def heartbeat(self, queue_id: str, claimed_by: str) -> bool:
        """Touch the heartbeat of one live claim; False when the claim is gone."""
        row = await self._fetch_one(HEARTBEAT_SQL, self._tenant_id, queue_id, claimed_by)
        return row is not None

    async def release(self, queue_id: str, claimed_by: str) -> dict[str, Any] | None:
        """Put one untouched claim back to pending (stop before the run)."""
        return await self._fetch_one(RELEASE_SQL, self._tenant_id, queue_id, claimed_by)

    async def finish(
        self,
        queue_id: str,
        claimed_by: str,
        *,
        run_id: str | None,
        pages_done: int | None,
        pages_total: int | None,
        chunks: int | None,
        last_error: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark one claim done with the pipeline's run id and counters."""
        return await self._fetch_one(
            FINISH_SQL,
            self._tenant_id,
            queue_id,
            run_id,
            pages_done,
            pages_total,
            chunks,
            last_error,
            claimed_by,
        )

    async def fail(
        self, queue_id: str, claimed_by: str, *, last_error: str
    ) -> dict[str, Any] | None:
        """Count one failed attempt; retry under max_attempts, else failed."""
        return await self._fetch_one(FAIL_SQL, self._tenant_id, queue_id, last_error, claimed_by)

    # --- connection scope ----------------------------------------------------

    async def _fetch_one(self, sql: str, *args: object) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            row = await conn.fetchrow(sql, *args)
        return dict(row) if row is not None else None

    async def _fetch_many(self, sql: str, *args: object) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", self._tenant_id
            )
            rows = await conn.fetch(sql, *args)
        return [dict(row) for row in rows]
