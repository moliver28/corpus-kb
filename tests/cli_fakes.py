"""Shared fakes for the CLI-leg tests.

``FakePool`` is the minimal pool double the CLI command flows use: they
only ever close it (the tool layer is faked at its own seams).

``FakeQueuePool`` is the stateful double the Wave 2C worker tests use:
one in-memory ``ingest_queue`` table with the claim/heartbeat/finish/fail
semantics the store's SQL pins. It emulates the store contract, not SQL;
the statement text is pinned by source-inspection tests instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

QUEUE_ID = "00000000-0000-0000-0000-0000000000e1"
TENANT = "00000000-0000-0000-0000-000000000003"
EPOCH = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


class FakePool:
    """Pool double: the CLI flows only ever close it (tools are faked)."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def queue_row(status: str = "pending", **over: Any) -> dict[str, Any]:
    """One full ingest_queue row shape (the W1 pin, plus progress keys)."""
    row: dict[str, Any] = {
        "id": QUEUE_ID,
        "tenant_id": TENANT,
        "path": "/tmp/book.pdf",
        "title": None,
        "project_id": None,
        "profile": None,
        "strategy_config": "{}",
        "status": status,
        "attempts": 0,
        "max_attempts": 3,
        "last_error": None,
        "run_id": None,
        "content_hash": "hash-1",
        "claimed_by": None,
        "heartbeat_at": None,
        "queued_at": EPOCH,
        "started_at": None,
        "finished_at": None,
        "updated_at": None,
        "pages_done": None,
        "pages_total": None,
        "chunks": None,
    }
    row.update(over)
    return row


class _FakeQueueConn:
    """Connection double: routes queue statements to the in-memory table."""

    def __init__(self, pool: FakeQueuePool) -> None:
        self._pool = pool

    def transaction(self) -> _FakeQueueConn:
        return self

    async def __aenter__(self) -> _FakeQueueConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        if "FOR UPDATE SKIP LOCKED" in sql:
            return self._pool._claim(args[1], args[2])
        if "heartbeat_at = now()" in sql:
            return self._pool._heartbeat(*args[1:])
        if "status = 'pending', claimed_by = NULL" in sql:
            return self._pool._release(*args[1:])
        if "pages_total = $3" in sql:
            return self._pool._pages_total(*args[1:])
        if "status = 'done'" in sql:
            return self._pool._finish(*args[1:])
        if "attempts + 1" in sql:
            return self._pool._fail(*args[1:])
        return None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        if "make_interval" in sql:
            return self._pool._reclaim(args[1])
        return []


class FakeQueuePool:
    """One in-memory queue table with the worker-facing store semantics.

    Emulates: oldest-pending claim (SKIP LOCKED stands in for concurrency),
    live heartbeats, stale reclaim without an attempt bump, release, the
    done shape with progress counters, and the attempt ladder (pending
    while attempts stay under max_attempts, else failed). ``now`` is
    mutable so tests can age a lease without sleeping.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.now = EPOCH
        self.claims: list[str] = []
        self.closed = False

    def acquire(self) -> _FakeQueueConn:
        return _FakeQueueConn(self)

    async def close(self) -> None:
        self.closed = True

    # --- statement emulations (args after the tenant id) -------------------

    def _claim(self, claimed_by: object, claimed_before: Any = None) -> dict[str, Any] | None:
        pending = [
            r
            for r in self.rows
            if r["status"] == "pending"
            and (
                r["started_at"] is None
                or claimed_before is None
                or r["started_at"] < claimed_before
            )
        ]
        if not pending:
            return None
        row = sorted(pending, key=lambda r: (r["queued_at"], r["id"]))[0]
        row["status"] = "running"
        row["claimed_by"] = str(claimed_by)
        row["heartbeat_at"] = self.now
        # The database stamps started_at at claim time, never before the
        # worker's pass watermark: mirror that so a pass cannot re-claim.
        row["started_at"] = max(
            row["started_at"] or self.now,
            claimed_before if isinstance(claimed_before, datetime) else self.now,
        )
        self.claims.append(row["id"])
        return dict(row)

    def _heartbeat(self, queue_id: str, claimed_by: str) -> dict[str, Any] | None:
        row = self._live(queue_id, str(claimed_by))
        if row is None:
            return None
        row["heartbeat_at"] = self.now
        return {"id": row["id"]}

    def _pages_total(
        self, queue_id: str, pages_total: int, claimed_by: str
    ) -> dict[str, Any] | None:
        row = self._live(queue_id, claimed_by)
        if row is None:
            return None
        row["pages_total"] = pages_total
        return dict(row)

    def _reclaim(self, stale_seconds: float) -> list[dict[str, Any]]:
        cutoff = self.now - timedelta(seconds=float(stale_seconds))
        stale = [
            r
            for r in self.rows
            if r["status"] == "running"
            and r["heartbeat_at"] is not None
            and r["heartbeat_at"] < cutoff
        ]
        for row in stale:
            row["status"] = "pending"
            row["claimed_by"] = None
            row["heartbeat_at"] = None
            row["last_error"] = "The worker lease ran out. The row is pending again."
        return [dict(r) for r in stale]

    def _release(self, queue_id: str, claimed_by: str) -> dict[str, Any] | None:
        row = self._live(queue_id, claimed_by)
        if row is None:
            return None
        row["status"] = "pending"
        row["claimed_by"] = None
        row["heartbeat_at"] = None
        return dict(row)

    def _finish(
        self,
        queue_id: str,
        run_id: str | None,
        pages_done: int | None,
        pages_total: int | None,
        chunks: int | None,
        last_error: str | None,
        claimed_by: str,
    ) -> dict[str, Any] | None:
        row = self._live(queue_id, claimed_by)
        if row is None:
            return None
        row["status"] = "done"
        row["run_id"] = run_id or row["run_id"]
        row["pages_done"] = pages_done
        # The SQL COALESCEs pages_total, so the claim-time probe fill
        # survives a finish that answers NULL.
        row["pages_total"] = pages_total if pages_total is not None else row["pages_total"]
        row["chunks"] = chunks
        row["last_error"] = last_error
        row["claimed_by"] = None
        row["heartbeat_at"] = None
        row["finished_at"] = self.now
        return dict(row)

    def _fail(self, queue_id: str, last_error: str, claimed_by: str) -> dict[str, Any] | None:
        row = self._live(queue_id, claimed_by)
        if row is None:
            return None
        row["attempts"] += 1
        row["last_error"] = last_error
        row["claimed_by"] = None
        row["heartbeat_at"] = None
        if row["attempts"] < row["max_attempts"]:
            row["status"] = "pending"
        else:
            row["status"] = "failed"
            row["finished_at"] = self.now
        return dict(row)

    def _live(self, queue_id: str, claimed_by: str) -> dict[str, Any] | None:
        for row in self.rows:
            if (
                row["id"] == queue_id
                and row["status"] == "running"
                and row["claimed_by"] == claimed_by
            ):
                return row
        return None
