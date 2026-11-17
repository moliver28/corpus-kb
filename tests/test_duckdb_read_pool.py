"""Read-pool tests (issue #471).

Contract under test: reads run in parallel only while NO transaction is
open; during a transaction they queue behind it and observe post-commit
state (the pinned W0 test
``test_statement_from_another_task_waits_for_the_tx_scope`` stays the
authority). Further contracts: bounded overlap latch, exhaustion
backpressure, poisoned-cursor recovery, factory-failure honesty,
cancellation safety, close-wakes-parked-readers, and one unified
post-close error surface for both gateway shapes.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest

from corpus_kb.storage.duckdb_read_pool import POST_CLOSE_MESSAGE, DuckDbReadPool
from corpus_kb.storage.gateway import DuckDbGateway

if TYPE_CHECKING:
    from pathlib import Path


class _FakeCursor:
    """Minimal cursor stand-in: closable, otherwise inert."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


async def _wait_event(event: threading.Event, timeout: float = 5.0) -> None:
    await asyncio.to_thread(event.wait, timeout)


async def test_overlap_latch_concurrency_and_bound() -> None:
    """Five readers against pool size 4: the latch group stays capped.

    parties=5 makes an unbounded pool pass (all five meet the barrier);
    a bounded pool can never seat five at once, so at least one reader
    must starve and break. Which four latch is scheduler-dependent, so
    the assertions pin the invariant, not one interleaving.
    """
    pool: DuckDbReadPool[_FakeCursor] = DuckDbReadPool(_FakeCursor, size=4)
    barrier = threading.Barrier(5, timeout=3)
    outcomes: list[str] = []

    async def reader() -> None:
        try:
            await pool.run(lambda _conn: barrier.wait())
        except threading.BrokenBarrierError:
            outcomes.append("broken")
        else:
            outcomes.append("ok")

    tasks = [asyncio.create_task(reader()) for _ in range(5)]
    await asyncio.sleep(0.2)  # every growth decision has happened by now
    assert pool.live <= 4, "pool width is a hard bound"
    await asyncio.gather(*tasks)
    assert outcomes.count("ok") <= 4, outcomes
    assert outcomes.count("broken") >= 1, outcomes


async def test_pool_exhaustion_backpressure() -> None:
    """With width 1 a second reader waits for the first cursor's return."""
    pool: DuckDbReadPool[_FakeCursor] = DuckDbReadPool(_FakeCursor, size=1)
    release = threading.Event()
    started = threading.Event()

    def work(_conn: _FakeCursor) -> str:
        started.set()
        release.wait(timeout=5)
        return "done"

    first = asyncio.create_task(pool.run(work))
    await _wait_event(started)
    second = asyncio.create_task(pool.run(lambda _conn: "second"))
    await asyncio.sleep(0.05)
    assert not second.done(), "exhausted reader must wait, not race the pool"
    release.set()
    assert await first == "done"
    assert await second == "second"
    assert pool.live == 1


async def test_poisoned_cursor_recovery(duckdb_path: Path) -> None:
    """A failing read closes its cursor; the pool regrows and serves again."""
    import duckdb

    gateway = await DuckDbGateway.connect(str(duckdb_path), read_pool_size=2)
    try:
        await gateway.execute("CREATE TABLE t (a INTEGER)")
        with pytest.raises(duckdb.CatalogException):
            await gateway.fetch("SELECT * FROM no_such_table")
        pool = gateway.read_pool
        assert pool is not None
        assert pool.closed_count == 1
        # Lazy width: one cursor was created, poisoned, and regrown, so the
        # pool owns exactly one healthy cursor here, never a poisoned one.
        assert pool.live == 1
        rows = await gateway.fetch("SELECT 1 AS v")
        assert rows and rows[0]["v"] == 1
    finally:
        await gateway.close()


async def test_factory_failure_does_not_inflate_live() -> None:
    """A factory error leaves the count honest; the pool still recovers."""
    calls = 0

    def flaky_factory() -> _FakeCursor:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("driver hiccup")
        return _FakeCursor()

    pool: DuckDbReadPool[_FakeCursor] = DuckDbReadPool(flaky_factory, size=2)
    with pytest.raises(RuntimeError, match="driver hiccup"):
        await pool.run(lambda _conn: "unused")
    assert pool.live == 0, "phantom live count would park later readers"
    result = await pool.run(lambda _conn: "second")
    assert result == "second"
    assert pool.live == 1
    assert calls == 2


async def test_transaction_queues_foreign_reads_and_keeps_scope_readback(
    duckdb_path: Path,
) -> None:
    """Foreign reads during an open tx WAIT and see post-commit state.

    The scope owner's own read-back still sees its uncommitted row (W0).
    This mirrors the pinned
    ``test_statement_from_another_task_waits_for_the_tx_scope`` contract
    at the pool-routing layer.
    """
    gateway = await DuckDbGateway.connect(str(duckdb_path), read_pool_size=2)
    try:
        await gateway.execute("CREATE TABLE s (a INTEGER)")
        scope_entered = asyncio.Event()
        release_scope = threading.Event()
        reader_result: list[int] = []

        async def writer() -> None:
            async with gateway.transaction("00000000-0000-0000-0000-000000000001"):
                await gateway.execute("INSERT INTO s VALUES (7)")
                # Owner read-back: the scope sees its own uncommitted row.
                inside = await gateway.fetch("SELECT a FROM s")
                assert [row["a"] for row in inside] == [7]
                scope_entered.set()
                await _wait_event(release_scope)

        async def reader() -> None:
            await scope_entered.wait()
            rows = await gateway.fetch("SELECT a FROM s")
            reader_result.extend(row["a"] for row in rows)

        writer_task = asyncio.create_task(writer())
        reader_task = asyncio.create_task(reader())
        await scope_entered.wait()
        await asyncio.sleep(0.1)
        assert not reader_task.done(), "foreign read must queue behind the scope"
        release_scope.set()
        await writer_task
        await asyncio.wait_for(reader_task, timeout=5)
        assert reader_result == [7], "queued read observes post-commit state"
    finally:
        await gateway.close()


async def test_cancelled_read_never_closes_a_running_cursor() -> None:
    """Cancelling a reader leaves the cursor whole and the pool healthy."""
    pool: DuckDbReadPool[_FakeCursor] = DuckDbReadPool(_FakeCursor, size=1)
    release = threading.Event()
    started = threading.Event()

    def work(_conn: _FakeCursor) -> str:
        started.set()
        release.wait(timeout=5)
        return "late"

    task = asyncio.create_task(pool.run(work))
    await _wait_event(started)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.closed_count == 0, "cursor must outlive the cancelled caller"
    release.set()
    # The done-callback returns the cursor once the orphan thread ends; the
    # next read parks until that happens, so this await IS the sync point.
    assert await pool.run(lambda _conn: "next") == "next"
    assert pool.closed_count == 0
    assert pool.live == 1


async def test_close_wakes_parked_readers() -> None:
    """close() releases a parked reader with the post-close error, fast."""
    pool: DuckDbReadPool[_FakeCursor] = DuckDbReadPool(_FakeCursor, size=1)
    release = threading.Event()
    started = threading.Event()

    def work(_conn: _FakeCursor) -> str:
        started.set()
        release.wait(timeout=5)
        return "holder"

    holder = asyncio.create_task(pool.run(work))
    await _wait_event(started)
    parked = asyncio.create_task(pool.run(lambda _conn: "parked"))
    await asyncio.sleep(0.05)
    assert not parked.done()
    await pool.close()
    with pytest.raises(RuntimeError, match=POST_CLOSE_MESSAGE):
        await asyncio.wait_for(parked, timeout=5)
    release.set()
    assert await holder == "holder"
    assert pool.closed_count == 1, "post-close release closes the held cursor"


async def test_close_surface_unified_across_shapes(duckdb_path: Path) -> None:
    """Both gateway shapes raise the same post-close error on reads."""
    file_gateway = await DuckDbGateway.connect(str(duckdb_path), read_pool_size=1)
    await file_gateway.close()
    with pytest.raises(RuntimeError, match=POST_CLOSE_MESSAGE):
        await file_gateway.fetch("SELECT 1")

    memory_gateway = await DuckDbGateway.connect(":memory:", read_pool_size=1)
    await memory_gateway.close()
    with pytest.raises(RuntimeError, match=POST_CLOSE_MESSAGE):
        await memory_gateway.fetch("SELECT 1")


async def test_fetch_shapes_unchanged(duckdb_path: Path) -> None:
    """fetchrow/fetchval/count parity on the write path is untouched."""
    gateway = await DuckDbGateway.connect(str(duckdb_path), read_pool_size=2)
    try:
        await gateway.execute("CREATE TABLE t (a INTEGER)")
        assert await gateway.fetchrow("SELECT a FROM t") is None
        await gateway.execute("INSERT INTO t VALUES (5)")
        row = await gateway.fetchrow("SELECT a FROM t WHERE a = 5")
        assert row is not None and row["a"] == 5
        assert await gateway.fetchval("SELECT count(*) FROM t") == 1
        assert await gateway.fetchval("SELECT a FROM t WHERE a = 6") is None
    finally:
        await gateway.close()


async def test_memory_database_skips_pool() -> None:
    """``:memory:`` keeps the legacy single-connection path (no pool)."""
    gateway = await DuckDbGateway.connect(":memory:", read_pool_size=4)
    try:
        assert gateway.read_pool is None
        await gateway.execute("CREATE TABLE m (a INTEGER)")
        await gateway.execute("INSERT INTO m VALUES (1)")
        assert await gateway.fetchval("SELECT count(*) FROM m") == 1
    finally:
        await gateway.close()


async def test_pool_run_returns_cursor_exclusively(duckdb_path: Path) -> None:
    """Concurrent pool reads on one gateway return correct, fresh results."""
    gateway = await DuckDbGateway.connect(str(duckdb_path), read_pool_size=2)
    try:
        await gateway.execute("CREATE TABLE t (a INTEGER)")
        await gateway.execute("INSERT INTO t VALUES (1), (2), (3)")

        async def read_sum() -> int:
            rows = await gateway.fetch("SELECT sum(a) AS total FROM t")
            return int(rows[0]["total"])

        totals = await asyncio.gather(*(read_sum() for _ in range(6)))
        assert totals == [6] * 6
    finally:
        await gateway.close()
