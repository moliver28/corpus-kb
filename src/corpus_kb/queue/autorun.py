"""The server autorun drain (textbook-ingestion program, Wave 3).

When ``queue.autorun`` is true, the HTTP server starts ONE background
drain task over the SAME ``QueueWorker`` loop the CLI drives — one
operation, one handler, a second surface only in wiring. The default
false starts nothing, so server behavior is unchanged. Postgres only:
the worker's typed backend refusal covers DuckDB, and a refusal logs
one line while the server continues (the mount_spa contract).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from corpus_kb.errors import ConfigError
from corpus_kb.utils.config_sections import mapping_section

from .worker import QueueWorker

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

#: How long a shutdown waits for the current book before the cancel. A
#: cancelled book leaves its row running; the stale lease reclaims it.
STOP_GRACE_SECONDS = 30.0

#: The default tenant the projections already drain (the queue table is
#: tenant-scoped, and the autorun serves the same default tenant).
DEFAULT_AUTORUN_TENANT = "00000000-0000-0000-0000-000000000001"

__all__ = ["QueueAutorun", "start_queue_autorun"]


@dataclass(frozen=True)
class QueueAutorun:
    """One drain task with its worker, for the clean server shutdown."""

    worker: QueueWorker
    task: asyncio.Task[None]

    async def stop(self) -> None:
        """Ask the worker to stop, wait a grace period, then cancel.

        The stop flag finishes the current book and ends the watch loop;
        the cancel covers a book that outlives the grace period.
        """
        self.worker.request_stop("server shutdown")
        try:
            await asyncio.wait_for(self.task, timeout=STOP_GRACE_SECONDS)
            return
        except TimeoutError:
            return  # wait_for already cancelled the task
        except asyncio.CancelledError:
            # The shutdown itself was cancelled: stop the drain, then
            # hand the cancel back to the caller.
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            raise


async def _drain_forever(worker: QueueWorker) -> None:
    """The logged exception consumer around the worker's watch loop."""
    try:
        await worker.watch()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("The queue drain stopped: %s", exc)


def start_queue_autorun(
    pool: asyncpg.Pool,
    config: dict[str, object],
    *,
    tenant_id: str = DEFAULT_AUTORUN_TENANT,
) -> QueueAutorun | None:
    """Start one background drain task when ``queue.autorun`` is true.

    Returns None — with no task started — when the flag is off (the
    default) or the worker refuses the database backend. The caller
    keeps the returned handle for its shutdown path.
    """
    if not mapping_section(config, "queue").get("autorun", False):
        return None
    try:
        worker = QueueWorker(pool=pool, tenant_id=tenant_id, config=config)
    except ConfigError as exc:
        remedy = getattr(exc, "remediation", "")
        logger.warning("Queue autorun stays off: %s. Remedy: %s", exc, remedy)
        return None
    task = asyncio.create_task(_drain_forever(worker), name="queue-autorun")
    logger.info("Queue autorun on: one drain loop polls every %s seconds", worker.poll_seconds)
    return QueueAutorun(worker=worker, task=task)
