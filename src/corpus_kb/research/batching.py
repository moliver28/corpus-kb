"""C5: bounded-concurrency batching and idempotent resumable run markers.

Two primitives for the embed/coding call loops:

* :func:`gather_bounded` — order-preserving gather with a semaphore. A
  worker only STARTS item i when a slot frees, so a thousand remote calls
  cannot be launched at once (backpressure), and results come back in input
  order regardless of completion order.
* :func:`load_completed_items` / :func:`mark_item_completed` /
  :func:`run_resumable` — the per-item completion marker stored in the
  ``research_runs.checkpoint`` JSONB (run_id keyed). Marks are idempotent
  (the UPDATE no-ops when the item is already recorded), so a crashed run
  resumes by skipping completed items and re-running only the rest.

No config key ships with this module on purpose (v8 ground rule 4: no inert
config): concurrency is a call-site parameter because the right bound is
per-backend (Ollama parallel slots, pool size), not per-deployment.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# House default: Ollama comfortably serves a handful of parallel generate
# calls on CPU; callers embedding against bigger backends raise it.
DEFAULT_CONCURRENCY = 4


class RunConn(Protocol):
    """Minimal asyncpg.Connection surface the run-marker helpers need."""

    async def fetchrow(self, sql: str, *args: object) -> object | None:
        """Run one statement, return the first row or None."""
        ...

    async def execute(self, sql: str, *args: object) -> object:
        """Run one statement."""
        ...


async def gather_bounded(
    items: Sequence[T],
    fn: Callable[[T], Awaitable[R]],
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[R]:
    """Run ``fn`` over ``items`` with at most ``concurrency`` in flight.

    Results are returned in INPUT order. Raises the first worker exception
    (after letting started workers finish — asyncio gathers them). A
    non-positive concurrency raises: silent serialization would hide a bug.
    """
    if concurrency <= 0:
        raise ValueError(f"concurrency must be a positive int; got {concurrency}")
    semaphore = asyncio.Semaphore(concurrency)

    async def _worker(index: int, item: T) -> tuple[int, R]:
        async with semaphore:
            value = await fn(item)
        return index, value

    tasks = [asyncio.ensure_future(_worker(i, item)) for i, item in enumerate(items)]
    results: list[R | None] = [None] * len(items)
    for task in asyncio.as_completed(tasks):
        index, value = await task
        results[index] = value
    return [value for value in results if value is not None]


# ---------------------------------------------------------------------------
# Resumable run markers (research_runs.checkpoint JSONB, run_id keyed)
# ---------------------------------------------------------------------------

COMPLETED_ITEMS_SQL = (
    "SELECT checkpoint->'completed' AS completed FROM research_runs "
    "WHERE run_id = $1 AND tenant_id = $2"
)

# Idempotent append: the UPDATE matches only when the item is NOT already in
# the completed array, so a re-mark is a no-op (UPDATE 0), never a duplicate.
MARK_ITEM_SQL = """
UPDATE research_runs
SET checkpoint = jsonb_set(
    coalesce(checkpoint, '{}'::jsonb),
    '{completed}',
    coalesce(checkpoint->'completed', '[]'::jsonb) || to_jsonb($3::text)
)
WHERE run_id = $1 AND tenant_id = $2
  AND NOT coalesce(checkpoint->'completed', '[]'::jsonb) @> to_jsonb($3::text)
"""


@dataclass(frozen=True)
class RunMarker:
    """Handle for one resumable run (tenant + run_id pair)."""

    tenant_id: str
    run_id: str


async def load_completed_items(conn: RunConn, marker: RunMarker) -> set[str]:
    """The set of item keys already recorded complete for this run."""
    row = await conn.fetchrow(COMPLETED_ITEMS_SQL, marker.run_id, marker.tenant_id)
    if row is None:
        return set()
    # asyncpg.Record satisfies the Mapping protocol; cast (not isinstance)
    # so dict and Record both flow through the same read.
    record = cast("Mapping[str, object]", row)
    completed = record.get("completed")
    if not isinstance(completed, (list, tuple)):
        return set()
    return {str(item) for item in completed}


async def mark_item_completed(conn: RunConn, marker: RunMarker, item_key: str) -> bool:
    """Record one item complete; True when the mark was newly appended."""
    result = await conn.execute(MARK_ITEM_SQL, marker.run_id, marker.tenant_id, item_key)
    status = result if isinstance(result, str) else str(result)
    return status.startswith("UPDATE 1")


async def run_resumable(
    conn: RunConn,
    marker: RunMarker,
    items: Sequence[T],
    key_of: Callable[[T], str],
    fn: Callable[[T], Awaitable[R]],
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[R]:
    """Run ``fn`` over the not-yet-completed items, marking each on success.

    Completed items are skipped (idempotent resume); results cover only the
    items THIS call executed, in input order of those items. A failed item
    is NOT marked, so the next resume retries it.
    """
    done = await load_completed_items(conn, marker)
    pending = [item for item in items if key_of(item) not in done]

    async def _run_and_mark(item: T) -> R:
        value = await fn(item)
        await mark_item_completed(conn, marker, key_of(item))
        return value

    return await gather_bounded(pending, _run_and_mark, concurrency)


def chunked(items: Iterable[T], size: int) -> list[list[T]]:
    """Split ``items`` into ordered chunks of at most ``size``."""
    if size <= 0:
        raise ValueError(f"chunk size must be a positive int; got {size}")
    chunk: list[T] = []
    chunks: list[list[T]] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            chunks.append(chunk)
            chunk = []
    if chunk:
        chunks.append(chunk)
    return chunks
