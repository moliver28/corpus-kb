"""Read-pool tests (issue #471).

Five contracts, per the wave plan: overlap latch (pool-size cursors run
concurrently), bounded exhaustion (surplus readers wait, then proceed),
transaction serialization preserved (scope reads see uncommitted rows on
the primary; pool reads see committed state only), poisoned-cursor
recovery (a failed read retires its cursor; the pool regrows), and the
fetch-shape regression (fetchrow/fetchval/count parity unchanged).
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest

from corpus_kb.storage.duckdb_read_pool import DuckDbReadPool
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
    """Pool-size readers latch together; readers past the bound wait."""
    pool = DuckDbReadPool(_FakeCursor, size=3)
    barrier = threading.Barrier(3, timeout=2)
    outcomes: list[str] = []

    async def reader() -> None:
        try:
            await pool.run(lambda _conn: barrier.wait())
        except threading.BrokenBarrierError:
            outcomes.append("broken")
        else:
            outcomes.append("ok")

    await asyncio.gather(*(reader() for _ in range(5)))
    assert outcomes.count("ok") == 3, outcomes
    assert outcomes.count("broken") == 2, outcomes


async def test_pool_exhaustion_backpressure() -> None:
    """With width 1 a second reader waits for the first cursor's return."""
    pool = DuckDbReadPool(_FakeCursor, size=1)
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


async def test_transaction_snapshot_and_scope_readback(duckdb_path: Path) -> None:
    """Scope reads see uncommitted rows; pool reads see committed only."""
    tenant = "00000000-0000-0000-0000-000000000001"
    gateway = await DuckDbGateway.connect(str(duckdb_path), read_pool_size=2)
    try:
        await gateway.execute("CREATE TABLE s (a INTEGER)")
        scope_entered = asyncio.Event()
        release_scope = threading.Event()

        async def writer() -> None:
            async with gateway.transaction(tenant):
                await gateway.execute("INSERT INTO s VALUES (7)")
                inside = await gateway.fetch("SELECT a FROM s")
                assert [row["a"] for row in inside] == [7]
                scope_entered.set()
                await _wait_event(release_scope)

        async def reader(entered: asyncio.Event, done: asyncio.Event) -> None:
            await entered.wait()
            outside = await gateway.fetch("SELECT a FROM s")
            assert outside == [], "pool read must not see the open scope"
            done.set()

        reader_done = asyncio.Event()
        writer_task = asyncio.create_task(writer())
        reader_task = asyncio.create_task(reader(scope_entered, reader_done))
        await asyncio.wait_for(reader_done.wait(), timeout=5)
        release_scope.set()
        await writer_task
        await reader_task
        committed = await gateway.fetch("SELECT a FROM s")
        assert [row["a"] for row in committed] == [7]
    finally:
        await gateway.close()


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
