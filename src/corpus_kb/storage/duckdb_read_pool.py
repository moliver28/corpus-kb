"""Bounded read-cursor pool for the DuckDB gateway (issue #471).

CONTRACT: reads run in parallel only while no transaction is open. During
a transaction, reads queue behind it and observe post-commit state. The
gateway routes on that rule; this module only ever serves the no-open-scope
case.

Reads borrow a cursor duplicated from the gateway's open database
instance (``DuckDBPyConnection.cursor()`` attaches to the SAME instance,
so the file keeps exactly one owner and the one-writer posture stands).
Writes and transaction scopes keep the primary connection, so their W0
semantics are untouched.

Healing: a read whose callable raises retires that cursor (closed after
its worker thread has finished) and the pool regrows on a later acquire;
a poisoned cursor is never handed out twice. Factory failures never
inflate ``_live`` — the count moves only after a cursor exists.

Exhaustion: surplus readers park on an ``asyncio.Queue``. ``close()``
drains the idle cursors, then wakes every parked reader with a sentinel;
parked readers raise the post-close error instead of hanging.

Lifecycle: ``run`` shields the worker future, so cancelling a caller
never closes a cursor while its thread is still executing; the cursor is
released or retired only from the worker's done-callback.

``live`` and ``closed_count`` are introspection hooks for tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Generic, Protocol, TypeVar, cast

T = TypeVar("T")
_W = TypeVar("_W")


class _Closeable(Protocol):
    """The one cursor capability the pool itself relies on."""

    def close(self) -> None: ...


C = TypeVar("C", bound=_Closeable)

#: Default pool width. Four cursors cover the daemon's read fan-out
#: (search, fetch, stats) without multiplying thread hand-offs.
DEFAULT_READ_POOL_SIZE = 4

#: One unified post-close message for every shape (file-backed and
#: ``:memory:``); the gateway raises the same on reads after close.
POST_CLOSE_MESSAGE = "read pool is closed"

_CLOSE_SENTINEL: object = object()


class DuckDbReadPool(Generic[C]):
    """Lend duplicated cursors to concurrent readers; bounded, self-healing."""

    def __init__(
        self,
        cursor_factory: Callable[[], C],
        size: int,
    ) -> None:
        self._factory = cursor_factory
        self._size = max(1, int(size))
        self._idle: asyncio.Queue[C] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._live = 0
        self._waiters = 0
        self._closed = False
        self.closed_count = 0

    @property
    def live(self) -> int:
        """Cursors this pool currently owns (idle plus in flight)."""
        return self._live

    async def run(self, fn: Callable[[C], T]) -> T:
        """Borrow one cursor, run ``fn(conn)`` in a worker thread, return it.

        Cancelling the caller does not close the cursor mid-execution: the
        worker future is shielded, and its done-callback releases (success)
        or retires (failure, cancellation) the cursor only once the thread
        has finished with it. A failure propagates unchanged.
        """
        conn = await self._acquire()
        worker = asyncio.ensure_future(asyncio.to_thread(fn, conn))
        worker.add_done_callback(lambda fut: self._on_worker_done(fut, conn))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Leave the worker running; the done-callback owns the cursor.
            raise

    async def close(self) -> None:
        """Stop lending; close idle cursors; wake parked readers.

        Every parked reader wakes with the sentinel and raises the
        post-close error. In-flight borrows retire their cursor from the
        worker done-callback (``_release`` closes it on a closed pool).
        """
        async with self._lock:
            self._closed = True
            idle: list[C] = []
            while True:
                try:
                    idle.append(self._idle.get_nowait())
                except asyncio.QueueEmpty:
                    break
            parked = self._waiters
        for conn in idle:
            if conn is not _CLOSE_SENTINEL:
                await self._close_cursor(conn)
        for _ in range(parked):
            # The queue is typed C; the sentinel is the one deliberate lie,
            # cast here so parked readers wake with the post-close error.
            self._idle.put_nowait(cast("C", _CLOSE_SENTINEL))

    async def _acquire(self) -> C:
        if self._closed:
            raise RuntimeError(POST_CLOSE_MESSAGE)
        async with self._lock:
            if self._closed:
                raise RuntimeError(POST_CLOSE_MESSAGE)
            try:
                return self._idle.get_nowait()
            except asyncio.QueueEmpty:
                pass
            if self._live < self._size:
                # Grow ONLY on success: a factory failure must not inflate
                # the live count into a phantom that later readers park on.
                conn = await asyncio.to_thread(self._factory)
                self._live += 1
                return conn
            self._waiters += 1
        try:
            conn = await self._idle.get()
        finally:
            self._waiters -= 1
        if conn is _CLOSE_SENTINEL:
            raise RuntimeError(POST_CLOSE_MESSAGE)
        if self._closed:
            self.closed_count += 1
            await self._close_cursor(conn)
            raise RuntimeError(POST_CLOSE_MESSAGE)
        return conn

    def _release(self, conn: C) -> None:
        if self._closed:
            self.closed_count += 1
            with suppress(Exception):
                conn.close()
            return
        self._idle.put_nowait(conn)

    def _on_worker_done(self, fut: asyncio.Future[_W], conn: C) -> None:
        """Release or retire the cursor strictly after the thread finished."""
        try:
            loop = fut.get_loop()
            if loop.is_closed():
                return
            if fut.cancelled() or fut.exception() is not None:
                loop.create_task(self._discard(conn))
            else:
                self._release(conn)
        except RuntimeError:
            return

    async def _discard(self, conn: C) -> None:
        """Retire a poisoned cursor and regrow for parked readers."""
        self.closed_count += 1
        await self._close_cursor(conn)
        async with self._lock:
            self._live -= 1
            if self._live < self._size and not self._closed:
                try:
                    fresh = await asyncio.to_thread(self._factory)
                except Exception:
                    return
                self._live += 1
                self._idle.put_nowait(fresh)

    @staticmethod
    async def _close_cursor(conn: C) -> None:
        def _close() -> None:
            with suppress(Exception):
                conn.close()

        await asyncio.to_thread(_close)
