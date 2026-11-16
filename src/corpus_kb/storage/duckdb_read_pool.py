"""Bounded read-cursor pool for the DuckDB gateway (issue #471).

Read-heavy calls borrow a cursor duplicated from the gateway's open
database instance (``DuckDBPyConnection.cursor()`` attaches to the SAME
instance, so the file keeps exactly one owner and the one-writer posture
stands). Writes and transaction scopes keep the primary connection, so
their W0 semantics are untouched. A pool read sees the last COMMITTED
state; it never sees another task's open, uncommitted scope.

Healing: a borrow whose callable raises closes that cursor and shrinks
the pool; the next acquire regrows it, so a poisoned cursor is never
handed out twice. Exhaustion backpressures on an ``asyncio.Queue`` —
surplus readers wait for a return instead of racing the file lock.
``live`` and ``closed_count`` are introspection hooks for tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:  # function-local at runtime (program rule 9: deptry-visible)
    import duckdb

T = TypeVar("T")

#: Default pool width. Four cursors cover the daemon's read fan-out
#: (search, fetch, stats) without multiplying thread hand-offs.
DEFAULT_READ_POOL_SIZE = 4


class DuckDbReadPool:
    """Lend duplicated cursors to concurrent readers; bounded, self-healing."""

    def __init__(
        self,
        cursor_factory: Callable[[], duckdb.DuckDBPyConnection],
        size: int,
    ) -> None:
        self._factory = cursor_factory
        self._size = max(1, int(size))
        self._idle: asyncio.Queue[duckdb.DuckDBPyConnection] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._live = 0
        self._closed = False
        self.closed_count = 0

    @property
    def live(self) -> int:
        """Cursors this pool currently owns (idle plus in flight)."""
        return self._live

    async def run(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        """Borrow one cursor, run ``fn(conn)`` in a worker thread, return it.

        A failure closes the borrowed cursor (never reused) before the
        error propagates unchanged to the caller.
        """
        conn = await self._acquire()
        try:
            result = await asyncio.to_thread(fn, conn)
        except BaseException:
            await self._discard(conn)
            raise
        self._release(conn)
        return result

    async def close(self) -> None:
        """Close idle cursors and stop lending. In-flight borrows discard."""
        self._closed = True
        while True:
            try:
                conn = self._idle.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._close_cursor(conn)
        async with self._lock:
            self._live = 0

    async def _acquire(self) -> duckdb.DuckDBPyConnection:
        if self._closed:
            raise RuntimeError("read pool is closed")
        try:
            return self._idle.get_nowait()
        except asyncio.QueueEmpty:
            pass
        async with self._lock:
            if self._live < self._size and not self._closed:
                self._live += 1
                return self._factory()
        return await self._idle.get()

    def _release(self, conn: duckdb.DuckDBPyConnection) -> None:
        if self._closed:
            self.closed_count += 1
            with suppress(Exception):
                conn.close()
            return
        self._idle.put_nowait(conn)

    async def _discard(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Retire a poisoned cursor and regrow the pool for parked waiters."""
        self.closed_count += 1
        await self._close_cursor(conn)
        async with self._lock:
            self._live -= 1
            if self._live < self._size and not self._closed:
                self._live += 1
                with suppress(Exception):
                    self._idle.put_nowait(self._factory())

    @staticmethod
    async def _close_cursor(conn: duckdb.DuckDBPyConnection) -> None:
        def _close() -> None:
            with suppress(Exception):
                conn.close()

        await asyncio.to_thread(_close)
