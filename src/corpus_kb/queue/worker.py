"""The ingest-queue drain worker (textbook-ingestion program, Wave 2C+3).

One loop: reclaim stale leases, claim the oldest pending row, run the
book through the EXISTING ingest pipeline entry (``ingest_tools`` ->
``run_pipeline`` — one operation, one handler, no second pipeline) with
the row's own settings, and record done/failed honestly. The claim
probes ``pages_total`` best-effort; the row's profile and strategy
config ride in through ONE per-book config copy (``worker_support``).
Success fills the run id and counters; failure retries under max_attempts.

One drain pass gives each row one attempt (the pass watermark), so a
failing book waits for the next pass, not a tight retry loop. Postgres
claims run under ``FOR UPDATE SKIP LOCKED``; duckdb is refused typed.
The stop flag finishes the current book and releases an unstarted claim.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from corpus_kb.utils.config_sections import mapping_section

from .report import (
    LAST_ERROR_CAP,
    BookOutcome,
    DrainReport,
    chunk_count_from,
    int_or_none,
    iso_or_empty,
    join_errors,
    report_status,
    str_or_none,
)
from .store import IngestQueueStore
from .worker_support import (
    StopFlag,
    WorkerIdentity,
    book_config_for,
    fill_pages_total,
    heartbeat,
    positive_float,
    positive_int,
    require_postgres_backend,
)

if TYPE_CHECKING:
    import asyncpg

__all__ = ["QueueWorker", "ingest_book"]

_HEARTBEAT_SLICES = 3


async def ingest_book(
    pool: asyncpg.Pool, config: dict[str, object], tenant_id: str, path: str
) -> dict[str, object]:
    """The ONE pipeline entry, called the way the ingest tools call it."""
    from corpus_kb.tools.ingest_tools import ingest_file

    return await ingest_file(path, pool, config, tenant_id)


class QueueWorker:
    """Claims queue rows and runs each book through the ingest pipeline."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        tenant_id: str,
        config: dict[str, object],
        identity: WorkerIdentity | None = None,
        beat_interval: float | None = None,
    ) -> None:
        self._pool = pool
        self._tenant_id = tenant_id
        self._config = config
        self._store = IngestQueueStore(pool, tenant_id)
        self._identity = identity or WorkerIdentity.for_this_process()
        self._beat_override = beat_interval
        self._pass_watermark = datetime.now(UTC)
        require_postgres_backend(config)
        queue_cfg = mapping_section(config, "queue")
        self.poll_seconds = positive_float(queue_cfg.get("poll_seconds"), 20.0)
        self.stale_lease_seconds = positive_float(queue_cfg.get("stale_lease_seconds"), 600.0)
        self.max_attempts = positive_int(queue_cfg.get("max_attempts"), 3)
        self.stop = StopFlag()

    def request_stop(self, reason: str = "stop requested") -> None:
        """Finish the current book, then end the pass."""
        self.stop.request(reason)

    async def drain_once(self) -> DrainReport:
        """One pass: claim and run books until the queue or the stop ends it."""
        started = time.monotonic()
        self._pass_watermark = datetime.now(UTC)
        report = DrainReport()
        await self._reclaim_stale(report)
        while not self.stop:
            row = await self._store.claim(
                self._identity.claimed_by, claimed_before=self._pass_watermark
            )
            if row is None:
                break
            if self.stop:
                await self._release(row, report)
                break
            await fill_pages_total(self._store, row, self._identity.claimed_by)
            report.add(await self._run_book(row))
        return self._close(report, started)

    async def watch(self) -> DrainReport:
        """Drain, then poll every poll_seconds until a stop lands."""
        started = time.monotonic()
        report = DrainReport()
        while not self.stop:
            pass_report = await self.drain_once()
            report.outcomes.extend(pass_report.outcomes)
            if pass_report.stopped:
                break
            await asyncio.sleep(self.poll_seconds)
        return self._close(report, started)

    # --- one book -------------------------------------------------------------

    async def _run_book(self, row: dict[str, object]) -> BookOutcome:
        queue_id, path = str(row.get("id")), str(row.get("path"))
        book_config = book_config_for(self._config, row)
        started = time.monotonic()
        lost: list[bool] = []

        def _mark_lost() -> None:
            lost.append(True)
            self.stop.request("the queue lease was lost")

        async with heartbeat(
            self._store, queue_id, self._identity.claimed_by, self._beat_interval(), _mark_lost
        ):
            try:
                result = await ingest_book(self._pool, book_config, self._tenant_id, path)
            except Exception as exc:
                return await self._record_failure(
                    row, queue_id, str(exc), time.monotonic() - started
                )
        if lost:
            # The claim is gone (cancel or reclaim); the row is not ours now.
            return BookOutcome(
                queue_id=queue_id,
                path=path,
                queued_at=iso_or_empty(row.get("queued_at")),
                status="pending",
                detail="the queue lease was lost during the run",
            )
        return await self._settle(row, queue_id, result, time.monotonic() - started)

    async def _settle(
        self, row: dict[str, object], queue_id: str, result: dict[str, object], elapsed: float
    ) -> BookOutcome:
        """Map one pipeline result onto done (with counters) or a failure."""
        status = str(result.get("status", "error"))
        if status in ("success", "partial"):
            return await self._record_done(row, queue_id, result, elapsed)
        message = str(result.get("message") or result.get("reason") or f"pipeline {status}")
        return await self._record_failure(row, queue_id, message, elapsed)

    async def _record_done(
        self, row: dict[str, object], queue_id: str, result: dict[str, object], elapsed: float
    ) -> BookOutcome:
        run_id = result.get("run_id")
        note = join_errors(result.get("errors"))
        updated = await self._store.finish(
            queue_id,
            self._identity.claimed_by,
            run_id=str(run_id) if run_id else None,
            pages_done=None,
            pages_total=None,
            chunks=chunk_count_from(result),
            last_error=note,
        )
        merged = updated or row
        return BookOutcome(
            queue_id=queue_id,
            path=str(merged.get("path") or row.get("path")),
            queued_at=iso_or_empty(merged.get("queued_at") or row.get("queued_at")),
            status=report_status(merged.get("status") or "done"),
            attempts=int_or_none(merged.get("attempts")),
            last_error=str_or_none(merged.get("last_error")) or note,
            run_id=str(run_id) if run_id else None,
            detail=None if updated is not None else "the claim changed during the run",
            elapsed_seconds=elapsed,
        )

    async def _record_failure(
        self, row: dict[str, object], queue_id: str, error: str, elapsed: float
    ) -> BookOutcome:
        updated = await self._store.fail(
            queue_id, self._identity.claimed_by, last_error=error[:LAST_ERROR_CAP]
        )
        merged = updated or row
        return BookOutcome(
            queue_id=queue_id,
            path=str(merged.get("path") or row.get("path")),
            queued_at=iso_or_empty(merged.get("queued_at") or row.get("queued_at")),
            status=report_status(merged.get("status") or "failed"),
            attempts=int_or_none(merged.get("attempts")),
            last_error=str_or_none(merged.get("last_error")) or error[:LAST_ERROR_CAP],
            detail=None if str(merged.get("status")) != "failed" else "the retries are spent",
            elapsed_seconds=elapsed,
        )

    async def _release(self, row: dict[str, object], report: DrainReport) -> None:
        queue_id = str(row.get("id"))
        await self._store.release(queue_id, self._identity.claimed_by)
        report.add(
            BookOutcome(
                queue_id=queue_id,
                path=str(row.get("path")),
                queued_at=iso_or_empty(row.get("queued_at")),
                status="pending",
                detail="released: stop landed before the run started",
            )
        )

    async def _reclaim_stale(self, report: DrainReport) -> None:
        for row in await self._store.reclaim_stale(self.stale_lease_seconds):
            report.add(
                BookOutcome(
                    queue_id=str(row.get("id")),
                    path=str(row.get("path")),
                    queued_at=iso_or_empty(row.get("queued_at")),
                    status="pending",
                    detail="reclaimed: the worker lease ran out",
                )
            )

    def _beat_interval(self) -> float:
        if self._beat_override is not None:
            return self._beat_override
        return max(1.0, min(self.poll_seconds, self.stale_lease_seconds / _HEARTBEAT_SLICES))

    def _close(self, report: DrainReport, started: float) -> DrainReport:
        report.elapsed_seconds = time.monotonic() - started
        report.outcomes.sort(key=lambda o: o.sort_key())
        if self.stop:
            report.stopped = True
            report.stop_reason = self.stop.reason or report.stop_reason
        return report
